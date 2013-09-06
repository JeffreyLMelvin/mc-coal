import datetime
import json
import logging

from pyoauth2.provider import AuthorizationProvider, ResourceAuthorization, ResourceProvider
from pyoauth2.utils import random_ascii_string, url_query_params

from google.appengine.ext import ndb

import webapp2
from webapp2_extras.routes import RedirectRoute

from wtforms import fields
from wtforms.ext.csrf.session import SessionSecureForm

from agar.auth import authentication_required

from config import coal_config
from user_auth import UserHandler, authenticate


class Client(ndb.Model):
    client_id = ndb.StringProperty(required=True)
    name = ndb.StringProperty()
    uri = ndb.StringProperty()
    logo_uri = ndb.StringProperty()
    redirect_uris = ndb.StringProperty(repeated=True)
    scope = ndb.StringProperty(repeated=True)
    secret = ndb.StringProperty()
    secret_expires_at = ndb.IntegerProperty(default=0)
    secret_expires = ndb.ComputedProperty(lambda self: datetime.datetime(year=1970, month=1, day=1) + datetime.timedelta(seconds=self.secret_expires_at) if self.secret_expires_at else None)
    registration_access_token = ndb.StringProperty()
    active = ndb.BooleanProperty(default=True)
    created = ndb.DateTimeProperty(auto_now_add=True)
    updated = ndb.DateTimeProperty(auto_now=True)

    @property
    def is_secret_expired(self):
        secret_expires = self.secret_expires
        return datetime.datetime.now() > secret_expires if secret_expires is not None else False

    def validate_secret(self, secret):
        if self.secret is not None and not self.is_secret_expired:
            return secret == self.secret
        return False

    @classmethod
    def get_by_client_id(self, client_id):
        key = ndb.Key(Client, client_id)
        return key.get()

    @classmethod
    def get_by_secret(cls, secret):
        return cls.query().filter(cls.secret == secret).get()


class AuthorizationCode(ndb.Model):
    code = ndb.StringProperty(required=True)
    client_id = ndb.StringProperty(required=True)
    user_key = ndb.KeyProperty(required=True)
    scope = ndb.StringProperty(repeated=True)
    expires_in = ndb.IntegerProperty(default=0)
    expires = ndb.ComputedProperty(lambda self: self.created + datetime.timedelta(seconds=self.expires_in) if self.expires_in else None)
    created = ndb.DateTimeProperty(auto_now_add=True)
    updated = ndb.DateTimeProperty(auto_now=True)

    @property
    def is_expired(self):
        return datetime.datetime.now() > self.expires if self.expires is not None else False


class Token(ndb.Model):
    access_token = ndb.StringProperty(required=True)
    refresh_token = ndb.StringProperty(required=True)
    client_id = ndb.StringProperty(required=True)
    user_key = ndb.KeyProperty(required=True)
    scope = ndb.StringProperty(repeated=True)
    token_type = ndb.StringProperty()
    expires_in = ndb.IntegerProperty(default=0)
    expires = ndb.ComputedProperty(lambda self: self.created + datetime.timedelta(seconds=self.expires_in) if self.expires_in is not None else None)
    created = ndb.DateTimeProperty(auto_now_add=True)
    updated = ndb.DateTimeProperty(auto_now=True)

    @property
    def is_expired(self):
        return datetime.datetime.now() > self.expires if self.expires is not None else False

    def validate_scope(self, scope):
        return scope in self.scope


class COALAuthorizationProvider(AuthorizationProvider):
    def get_authorization_code_key_name(self, client_id, code):
        return '{0}-{1}'.format(client_id, code)

    def _handle_exception(self, exc):
        logging.info(exc)

    @property
    def token_expires_in(self):
        return coal_config.OAUTH_TOKEN_EXPIRES_IN

    def validate_client_id(self, client_id):
        client = Client.get_by_client_id(client_id)
        if client is not None and client.active:
            return True
        return False

    def validate_client_secret(self, client_id, client_secret):
        client = Client.get_by_client_id(client_id)
        if client is not None and client.active:
            return client.validate_secret(client_secret)
        return False

    def validate_redirect_uri(self, client_id, redirect_uri):
        client = Client.get_by_client_id(client_id)
        if client is not None and redirect_uri in client.redirect_uris:
            return True
        return False

    def validate_scope(self, client_id, scope):
        client = Client.get_by_client_id(client_id)
        if client is not None:
            return scope in client.scope
        return False

    def validate_access(self, client_id):
        return webapp2.get_request().user.is_client_id_authorized(client_id)

    def from_authorization_code(self, client_id, code, scope):
        key_name = self.get_authorization_code_key_name(client_id, code)
        key = ndb.Key(AuthorizationCode, key_name)
        auth_code = key.get()
        if auth_code is not None and auth_code.is_expired:
            key.delete()
            auth_code = None
        return auth_code

    def from_refresh_token(self, client_id, refresh_token, scope):
        token_query = Token.query()
        token_query = token_query.filter(Token.client_id == client_id)
        token_query = token_query.filter(Token.refresh_token == refresh_token)
        return token_query.get()

    def persist_authorization_code(self, client_id, code, scope):
        user = webapp2.get_request().user
        key_name = self.get_authorization_code_key_name(client_id, code)
        key = ndb.Key(AuthorizationCode, key_name)
        if key.get() is not None:
            raise Exception("duplicate_authorization_code")
        scope = scope.split() if scope else ['data']
        auth_code = AuthorizationCode(key=key, code=code, client_id=client_id, scope=scope, user_key=user.key, expires_in=coal_config.OAUTH_TOKEN_EXPIRES_IN)
        auth_code.put()

    def persist_token_information(self, client_id, scope, access_token, token_type, expires_in, refresh_token, data):
        auth_code = data
        key = ndb.Key(Token, access_token)
        if key.get() is not None:
            raise Exception("duplicate_access_token")
        if self.from_refresh_token(client_id, refresh_token, scope):
            raise Exception("duplicate_refresh_token")
        scope = scope.split() if scope else ['data']
        token = Token(key=key, access_token=access_token, refresh_token=refresh_token, client_id=client_id, user_key=auth_code.user_key, scope=scope, token_type=token_type, expires_in=expires_in)
        token.put()

    def discard_authorization_code(self, client_id, code):
        key_name = self.get_authorization_code_key_name(client_id, code)
        key = ndb.Key(AuthorizationCode, key_name)
        key.delete()

    def discard_refresh_token(self, client_id, refresh_token):
        token = self.from_refresh_token(client_id, refresh_token, None)
        if token is not None:
            token.key.delete()

    def discard_client_user_tokens(self, client_id, user_key):
        token_query = Token.query()
        token_query = token_query.filter(Token.client_id == client_id)
        token_query = token_query.filter(Token.user_key == user_key)
        keys = [key for key in token_query.iter(keys_only=True)]
        ndb.delete_multi(keys)

    @property
    def secret_expires_in(self):
        """Property method to get the secret expiration time in seconds. 0 (zero) means never expire.

        :rtype: int
        """
        return 0

    def generate_client_secret(self):
        """Generate a random client secret

        :rtype: str
        """
        return random_ascii_string(self.token_length)

    def generate_registration_access_token(self):
        """Generate a random registration access token.

        :rtype: str
        """
        return random_ascii_string(self.token_length)

    def generate_secret_expires_at(self):
        """Generate the Time at which the client_secret will expire or 0 if it will not expire.  The time
        is represented as the number of seconds from 1970-01-01T0:0:0Z as measured in UTC until now.

        :rtype: int
        """
        secret_expires_at = 0
        if self.secret_expires_in:
            secret_expires = datetime.datetime.now() + datetime.timedelta(seconds=self.secret_expires_in)
            expires_in = secret_expires - datetime.datetime(year=1970, month=1, day=1)
            secret_expires_at = expires_in.seconds
        return secret_expires_at

    def get_registration_client_uri(self, client_id):
        """Get the fully qualified URL of the client configuration endpoint for the client_id.

        :param client_id: Client ID.
        :type client_id: str
        :rtype: str
        """
        return webapp2.uri_for('oauth_client', client_id=client_id, _scheme='https')

    def from_client_id(self, client_id):
        """Return mixed data or None on invalid.

        :param client_id: Client ID.
        :type client_id: str
        """
        data = None
        client = Client.get_by_client_id(client_id)
        if client is not None:
            data = {
                'client_id': client.client_id,
                'redirect_uris': client.redirect_uris,
                'scope': ' '.join(client.scope),
                'client_secret': client.secret,
                'client_secret_expires_at': client.secret_expires_at,
                'registration_access_token': client.registration_access_token,
                'registration_client_uri': self.get_registration_client_uri(client_id)
            }
            if client.name is not None:
                data['client_name'] = client.name
            if client.uri is not None:
                data['client_uri'] = client.uri
            if client.logo_uri is not None:
                data['logo_uri'] = client.logo_uri
        return data

    def persist_client_information(
        self, client_id, redirect_uris, client_name, client_uri, logo_uri,
        scope=None, client_secret=None, client_secret_expires_at=None, registration_access_token=None
    ):
        data = {
            'redirect_uris': redirect_uris or [],
            'name': client_name,
            'uri': client_uri,
            'logo_uri': logo_uri,
        }
        if scope is not None:
            data['scope'] = scope.split()
        if client_secret is not None:
            data['secret'] = client_secret
        if client_secret_expires_at is not None:
            data['secret_expires_at'] = client_secret_expires_at
        if registration_access_token is not None:
            data['registration_access_token'] = registration_access_token
        client = Client.get_by_client_id(client_id)
        if client is None:
            data['scope'] = ['data']
            client = Client(key=ndb.Key(Client, client_id), client_id=client_id, **data)
        else:
            client.populate(**data)
        client.put()

    def persist_client_secret(self, client_id, client_secret, client_secret_expires_at):
        client = Client.get_by_client_id(client_id)
        if client is not None:
            client.secret = client_secret
            client.secret_expires_at = client_secret_expires_at
            client.put()

    def discard_client_tokens(self, client_id):
        token_query = Token.query()
        token_query = token_query.filter(Token.client_id == client_id)
        keys = [key for key in token_query.iter(keys_only=True)]
        ndb.delete_multi(keys)

    def discard_client_information(self, client_id):
        key = ndb.Key(Client, client_id)
        key.delete()
        self.discard_client_tokens(client_id)

    def get_default_client_scope(self):
        return 'data'

    def get_authorization_header(self):
        return webapp2.get_request().headers.get('Authorization', None)

    def validate_registration_access_token(self, client_id, registration_access_token):
        client = Client.get_by_client_id(client_id)
        if client is not None and client.registration_access_token == registration_access_token:
            return True
        return False

    def validate_client_secret_expired(self, client_id):
        client = Client.get_by_client_id(client_id)
        if client is not None and client.is_secret_expired:
            return True
        return False

    def get_client_response(self, client_id, status_code=200):
        data = self.from_client_id(client_id)
        return self._make_json_response(data, status_code=status_code)

    def get_registration(self, **data):
        client_id = data.get('client_id', random_ascii_string(10))
        while self.validate_client_id(client_id):
            client_id = u'{0}-{1}'.format(client_id, random_ascii_string(1))
        redirect_uris = data.get('redirect_uris')
        client_name = data.get('client_name', None)
        client_uri = data.get('client_uri', None)
        logo_uri = data.get('logo_uri', None)
        scope = self.get_default_client_scope()
        client_secret = self.generate_client_secret()
        client_secret_expires_at = self.generate_secret_expires_at()
        registration_access_token = self.generate_registration_access_token()
        self.persist_client_information(
            client_id, redirect_uris, client_name, client_uri, logo_uri, scope,
            client_secret, client_secret_expires_at, registration_access_token
        )
        return self.get_client_response(client_id, status_code=201)

    def _make_error_response(self, error, headers=None, status_code=401):
        """Return an error response object from the given error code.

        :param error: The error code.
        :type data: str
        :param headers: Dict of headers to include in the requests.
        :type headers: dict
        :param status_code: HTTP status code.
        :type status_code: int
        :rtype: requests.Response
        """
        response_headers = {}
        if headers is not None:
            response_headers.update(headers)
        response_headers['WWW-Authenticate'] = 'Bearer error="{0}"'.format(error)
        return self._make_response(headers=response_headers, status_code=status_code)

    def get_registration_from_post_body(self, body):
        """Get a registration response from POST data.

        :param data: POST data containing authorization information.
        :type data: dict
        :rtype: requests.Response
        """
        try:
            # Load the
            data = json.loads(body)

            # Verify OAuth 2.0 Parameters
            for x in ['redirect_uris']:
                if not data.get(x):
                    raise TypeError("Missing required OAuth 2.0 POST param: {0}".format(x))

            # Handle get token from authorization code
            return self.get_registration(**data)
        except TypeError as exc:
            self._handle_exception(exc)

            # Catch missing parameters in request
            return self._make_json_error_response('invalid_request')
        except StandardError as exc:
            self._handle_exception(exc)

            # Catch all other server errors
            return self._make_json_error_response('server_error')

    def get_registration_access_token(self):
        registration_access_token = None
        header = self.get_authorization_header()
        header = header.split() if header is not None else None
        if header is not None and len(header) > 1 and header[0] == 'Bearer':
            registration_access_token = header[1]
        return registration_access_token

    def get_client(self, client_id):
        registration_access_token = self.get_registration_access_token()
        is_valid_client_id = self.validate_client_id(client_id)
        if not is_valid_client_id:
            return self._make_response(self, status_code=401)
        is_valid_registration_access_token = self.validate_registration_access_token(client_id, registration_access_token)
        if not is_valid_registration_access_token:
            return self._make_error_response('invalid_token')
        # Regenerate secret if expired
        is_secret_expired = self.validate_client_secret_expired(client_id)
        if is_secret_expired:
            client_secret = self.generate_client_secret()
            client_secret_expires_at = self.generate_secret_expires_at()
            self.persist_client_secret(client_id, client_secret, client_secret_expires_at)
        return self.get_client_response(client_id)

    def get_client_from_put_body(self, client_id, body):
        registration_access_token = self.get_registration_access_token()
        try:
            data = json.loads(body)

            # Verify OAuth 2.0 Parameters
            for x in ['client_id', 'redirect_uris']:
                if not data.get(x):
                    raise TypeError("Missing required OAuth 2.0 PUT param: {0}".format(x))

            is_valid_client_id = self.validate_client_id(client_id)
            if not is_valid_client_id:
                return self._make_response(self, status_code=401)
            is_valid_registration_access_token = self.validate_registration_access_token(client_id, registration_access_token)
            if not is_valid_registration_access_token:
                return self._make_error_response('invalid_token')

            if client_id != data['client_id']:
                return self._make_json_error_response('invalid_client_id')

            client_secret = data.get('client_secret', None)
            if client_secret is not None:
                is_valid_secret = self.validate_client_secret(client_id, client_secret)
                if not is_valid_secret:
                    return self._make_json_error_response('invalid_request')

            scope = data.get('scope', None)
            if scope is not None:
                is_valid_scope = self.validate_scope(client_id, scope)
                if not is_valid_scope:
                    return self._make_json_error_response('invalid_request')

            redirect_uris = data.get('redirect_uris')
            client_name = data.get('client_name', None)
            client_uri = data.get('client_uri', None)
            logo_uri = data.get('logo_uri', None)
            # Regenerate secret if expired
            is_secret_expired = self.validate_client_secret_expired(client_id)
            client_secret = None
            client_secret_expires_at = None
            if is_secret_expired:
                client_secret = self.generate_client_secret()
                client_secret_expires_at = self.generate_secret_expires_at()
            self.persist_client_information(
                client_id, redirect_uris, client_name, client_uri, logo_uri, scope=scope,
                client_secret=client_secret, client_secret_expires_at=client_secret_expires_at
            )
            return self.get_client_response(client_id)

        except TypeError as exc:
            self._handle_exception(exc)

            # Catch missing parameters in request
            return self._make_json_error_response('invalid_request')
        except StandardError as exc:
            self._handle_exception(exc)

            # Catch all other server errors
            return self._make_json_error_response('server_error')

authorization_provider = COALAuthorizationProvider()


class COALResourceAuthorization(ResourceAuthorization):
    user_key = None


class COALResourceProvider(ResourceProvider):
    SCOPE = 'data'

    @property
    def authorization_class(self):
        return COALResourceAuthorization

    def get_authorization_header(self):
        return webapp2.get_request().headers.get('Authorization', None)

    def validate_access_token(self, access_token, authorization):
        key = ndb.Key(Token, access_token)
        token = key.get()
        if token is not None and not token.is_expired and token.validate_scope(self.SCOPE):
            authorization.is_valid = True
            authorization.client_id = token.client_id
            authorization.user_key = token.user_key
            if token.expires is not None:
                d = datetime.datetime.now() - token.expires
                authorization.expires_in = d.seconds

resource_provider = COALResourceProvider()


class BaseSecureForm(SessionSecureForm):
    SECRET_KEY = coal_config.SECRET_KEY
    TIME_LIMIT = datetime.timedelta(minutes=20)


class AuthForm(BaseSecureForm):
    grant = fields.SubmitField('Grant', id='submit_button')
    deny = fields.SubmitField('Deny', id='submit_button')


class PyOAuth2Base(object):
    @property
    def client_id(self):
        return url_query_params(self.request.url).get('client_id', None)

    @property
    def redirect_uri(self):
        return url_query_params(self.request.url).get('redirect_uri', None)

    def set_response(self, response):
        self.response.set_status(response.status_code)
        self.response.headers.update(response.headers)
        self.response.write(response.text)


class AuthorizationCodeHandler(UserHandler, PyOAuth2Base):
    def set_authorization_code_response(self):
        response = authorization_provider.get_authorization_code_from_uri(self.request.url)
        self.set_response(response)

    @authentication_required(authenticate=authenticate)
    def get(self):
        if self.user.is_client_id_authorized(self.client_id):
            self.user.unauthorize_client_id(self.client_id)
        form = AuthForm(csrf_context=self.session)
        context = {'url': self.request.url, 'form': form, 'client_id': self.client_id}
        self.render_template('auth.html', context=context)

    @authentication_required(authenticate=authenticate)
    def post(self):
        form = AuthForm(self.request.POST, csrf_context=self.session)
        if form.validate():
            if form.grant.data:
                self.user.authorize_client_id(self.client_id)
            else:
                self.user.unauthorize_client_id(self.client_id)
            self.set_authorization_code_response()
        elif form.csrf_token.errors:
            logging.error(form.csrf_token.errors)
        else:
            form = AuthForm(csrf_context=self.session)
            context = {'url': self.request.url, 'form': form, 'client_id': self.client_id}
            self.render_template('auth.html', context=context)


class TokenHandler(webapp2.RequestHandler, PyOAuth2Base):
    def set_access_token_response(self):
        response = authorization_provider.get_token_from_post_data(self.request.POST)
        self.set_response(response)

    def post(self):
        self.set_access_token_response()


class RegistrationHandler(webapp2.RequestHandler, PyOAuth2Base):
    def set_registration_response(self):
        response = authorization_provider.get_registration_from_post_body(self.request.body)
        self.set_response(response)

    def post(self):
        self.set_registration_response()


class ClientHandler(webapp2.RequestHandler, PyOAuth2Base):
    def get(self, client_id):
        response = authorization_provider.get_client(client_id)
        self.set_response(response)

    def put(self, client_id):
        response = authorization_provider.get_client_from_put_body(client_id, self.request.body)
        self.set_response(response)

    def delete(self, client_id):
        registration_access_token = authorization_provider.get_registration_access_token()
        is_valid_client_id = authorization_provider.validate_client_id(client_id)
        if not is_valid_client_id:
            self.set_response(authorization_provider._make_response(status_code=401))
            return
        is_valid_registration_access_token = authorization_provider.validate_registration_access_token(client_id, registration_access_token)
        if not is_valid_registration_access_token:
            self.set_response(authorization_provider._make_error_response('invalid_token'))
            return
        authorization_provider.discard_client_information(client_id)
        del self.response.headers['Content-Type']
        self.response.set_status(204)


class ShowAuthorizationCodeHandler(UserHandler, PyOAuth2Base):
    @authentication_required(authenticate=authenticate)
    def get(self):
        code = self.request.GET.get('code', None)
        error = self.request.GET.get('error', None)
        context = {'code': code, 'error': error}
        self.render_template('oauth_show.html', context=context)


class TestHandler(webapp2.RequestHandler):
    def get(self):
        authorization = resource_provider.get_authorization()
        if not (authorization.is_oauth and authorization.is_valid):
            self.abort(401)
        self.response.headers['Content-Type'] = "application/json"
        client_id = authorization.client_id
        user = authorization.user_key.get()
        auth_header = self.request.headers.get('Authorization', None)
        body = u"{{'authorization': {0}, 'client_id': {1}".format(auth_header, client_id)
        if user:
            body += u", 'user': {0}}}".format(user.nickname)
        else:
            body = u"}}"
        self.response.set_status(200)
        self.response.out.write(body)


routes = [
    RedirectRoute('/oauth/auth', handler='oauth.AuthorizationCodeHandler', methods=['GET', 'POST'], name='oauth_auth'),
    RedirectRoute('/oauth/token', handler='oauth.TokenHandler', methods=['POST'], name='oauth_token'),
    RedirectRoute('/oauth/register', handler='oauth.RegistrationHandler', methods=['POST'], name='oauth_register'),
    RedirectRoute('/oauth/client/<client_id>', handler='oauth.ClientHandler', methods=['GET', 'PUT', 'DELETE'], name='oauth_client'),
    RedirectRoute('/oauth/show', handler='oauth.ShowAuthorizationCodeHandler', methods=['GET'], name='oauth_show'),
    RedirectRoute('/oauth/test', handler='oauth.TestHandler', methods=['GET'], name='oauth_test')
]
