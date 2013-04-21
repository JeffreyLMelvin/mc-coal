import os
import sys

for d in os.environ["PATH"].split(":"):
    dev_appserver_path = os.path.join(d, "dev_appserver.py")
    if os.path.isfile(dev_appserver_path):
        sdk_path = os.path.abspath(os.path.dirname(os.path.realpath(dev_appserver_path)))
        sys.path.insert(0, sdk_path)
        import dev_appserver
        dev_appserver.fix_sys_path()

from agar.test.base_test import BaseTest

import main
import models
import search

from test_api import ALL_LOG_LINES


class SearchTest(BaseTest):
    APPLICATION = main.application

    def setUp(self):
        super(SearchTest, self).setUp()
        self.log_lines = []
        for line in ALL_LOG_LINES:
            self.log_lines.append(models.LogLine.create(line, 'America/Chicago'))

    def test_search_log_lines(self):
        results, number_found, cursor = search.search_log_lines('gumptionthomas')
        self.assertEqual(4, len(results))
        self.assertEqual(4, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_log_lines('info')
        self.assertEqual(10, len(results))
        self.assertEqual(10, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_log_lines('vesicular')
        self.assertEqual(2, len(results))
        self.assertEqual(2, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_log_lines('yo')
        self.assertEqual(2, len(results))
        self.assertEqual(2, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_log_lines(models.CONNECTION_TAG)
        self.assertEqual(4, len(results))
        self.assertEqual(4, number_found)
        results, number_found, cursor = search.search_log_lines(models.SERVER_TAG)
        self.assertEqual(5, len(results))
        self.assertEqual(5, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_log_lines('2012-10-15')
        self.assertEqual(2, len(results))
        self.assertEqual(2, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_log_lines('username:gumptionthomas tags:{0}'.format(models.LOGIN_TAG))
        self.assertEqual(2, len(results))
        self.assertEqual(2, number_found)
        self.assertIsNotNone(cursor)

    def test_remove_log_lines(self):
        results, number_found, cursor = search.search_log_lines('info', limit=20)
        self.assertEqual(10, len(results))
        self.assertEqual(10, number_found)
        self.assertIsNotNone(cursor)
        for result in results:
            result.key.delete()
        results, number_found, cursor = search.search_log_lines('info')
        self.assertEqual(0, len(results))
        self.assertEqual(0, number_found)
        self.assertIsNone(cursor)

    def test_search_players(self):
        results, number_found, cursor = search.search_players('gumptionthomas')
        self.assertEqual(1, len(results))
        self.assertEqual(1, number_found)
        self.assertIsNotNone(cursor)
        results, number_found, cursor = search.search_players('vesicular')
        self.assertEqual(1, len(results))
        self.assertEqual(1, number_found)
        self.assertIsNotNone(cursor)

    def test_remove_players(self):
        results, number_found, cursor = search.search_players('gumptionthomas')
        self.assertEqual(1, len(results))
        self.assertEqual(1, number_found)
        self.assertIsNotNone(cursor)
        for result in results:
            result.key.delete()
        results, number_found, cursor = search.search_players('gumptionthomas')
        self.assertEqual(0, len(results))
        self.assertEqual(0, number_found)
        self.assertIsNone(cursor)
