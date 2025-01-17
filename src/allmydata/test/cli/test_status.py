"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from six import ensure_text

import os
import mock
import tempfile
from io import BytesIO, StringIO
from os.path import join

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.mutable.publish import MutableData
from allmydata.scripts.common_http import BadResponse
from allmydata.scripts.tahoe_status import _get_json_for_fragment
from allmydata.scripts.tahoe_status import _get_json_for_cap
from allmydata.scripts.tahoe_status import pretty_progress
from allmydata.scripts.tahoe_status import do_status
from allmydata.web.status import marshal_json

from allmydata.immutable.upload import UploadStatus
from allmydata.immutable.downloader.status import DownloadStatus
from allmydata.mutable.publish import PublishStatus
from allmydata.mutable.retrieve import RetrieveStatus
from allmydata.mutable.servermap import UpdateStatus
from allmydata.util import jsonbytes as json

from ..no_network import GridTestMixin
from ..common_web import do_http
from .common import CLITestMixin


class FakeStatus(object):
    def __init__(self):
        self.status = []

    def setServiceParent(self, p):
        pass

    def get_status(self):
        return self.status

    def get_storage_index(self):
        return None

    def get_size(self):
        return None


class ProgressBar(unittest.TestCase):

    def test_ascii0(self):
        prog = pretty_progress(80.0, size=10, output_ascii=True)
        self.assertEqual('########. ', prog)

    def test_ascii1(self):
        prog = pretty_progress(10.0, size=10, output_ascii=True)
        self.assertEqual('#.        ', prog)

    def test_ascii2(self):
        prog = pretty_progress(13.0, size=10, output_ascii=True)
        self.assertEqual('#o        ', prog)

    def test_ascii3(self):
        prog = pretty_progress(90.0, size=10, output_ascii=True)
        self.assertEqual('#########.', prog)

    def test_unicode0(self):
        self.assertEqual(
            pretty_progress(82.0, size=10, output_ascii=False),
            u'\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u258e ',
        )

    def test_unicode1(self):
        self.assertEqual(
            pretty_progress(100.0, size=10, output_ascii=False),
            u'\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588',
        )


class _FakeOptions(dict):
    def __init__(self):
        self._tmp = tempfile.mkdtemp()
        os.mkdir(join(self._tmp, 'private'), 0o777)
        with open(join(self._tmp, 'private', 'api_auth_token'), 'w') as f:
            f.write('a' * 32)
        with open(join(self._tmp, 'node.url'), 'w') as f:
            f.write('localhost:9000')

        self['node-directory'] = self._tmp
        self['verbose'] = True
        self.stdout = StringIO()
        self.stderr = StringIO()


class Integration(GridTestMixin, CLITestMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        yield super(Integration, self).setUp()
        self.basedir = "cli/status"
        self.set_up_grid()

        # upload something
        c0 = self.g.clients[0]
        data = MutableData(b"data" * 100)
        filenode = yield c0.create_mutable_file(data)
        self.uri = filenode.get_uri()

        # make sure our web-port is actually answering
        yield do_http("get", 'http://127.0.0.1:{}/status?t=json'.format(self.client_webports[0]))

    def test_simple(self):
        d = self.do_cli('status')# '--verbose')

        def _check(ign):
            code, stdout, stderr = ign
            self.assertEqual(code, 0, stderr)
            self.assertTrue('Skipped 1' in stdout)
        d.addCallback(_check)
        return d

    @defer.inlineCallbacks
    def test_help(self):
        rc, _, _ = yield self.do_cli('status', '--help')
        self.assertEqual(rc, 0)


class CommandStatus(unittest.TestCase):
    """
    These tests just exercise the renderers and ensure they don't
    catastrophically fail.

    They could be enhanced to look for "some" magic strings in the
    results and assert they're in the output.
    """

    def setUp(self):
        self.options = _FakeOptions()

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    @mock.patch('sys.stdout', StringIO())
    def test_no_operations(self, http):
        values = [
            StringIO(ensure_text(json.dumps({
                "active": [],
                "recent": [],
            }))),
            StringIO(ensure_text(json.dumps({
                "counters": {
                    "bytes_downloaded": 0,
                },
                "stats": {
                    "node.uptime": 0,
                }
            }))),
        ]
        http.side_effect = lambda *args, **kw: values.pop(0)
        do_status(self.options)

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    @mock.patch('sys.stdout', StringIO())
    def test_simple(self, http):
        recent_items = active_items = [
            UploadStatus(),
            DownloadStatus(b"abcd", 12345),
            PublishStatus(),
            RetrieveStatus(),
            UpdateStatus(),
            FakeStatus(),
        ]
        values = [
            BytesIO(json.dumps({
                "active": list(
                    marshal_json(item)
                    for item
                    in active_items
                ),
                "recent": list(
                    marshal_json(item)
                    for item
                    in recent_items
                ),
            }).encode("utf-8")),
            BytesIO(json.dumps({
                "counters": {
                    "bytes_downloaded": 0,
                },
                "stats": {
                    "node.uptime": 0,
                }
            }).encode("utf-8")),
        ]
        http.side_effect = lambda *args, **kw: values.pop(0)
        do_status(self.options)

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    def test_fetch_error(self, http):

        def boom(*args, **kw):
            raise RuntimeError("boom")
        http.side_effect = boom
        do_status(self.options)


class JsonHelpers(unittest.TestCase):

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    def test_bad_response(self, http):
        http.return_value = BadResponse('the url', 'some err')
        with self.assertRaises(RuntimeError) as ctx:
            _get_json_for_fragment({'node-url': 'http://localhost:1234'}, '/fragment')
        self.assertTrue(
            "Failed to get" in str(ctx.exception)
        )

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    def test_happy_path(self, http):
        http.return_value = StringIO('{"some": "json"}')
        resp = _get_json_for_fragment({'node-url': 'http://localhost:1234/'}, '/fragment/')
        self.assertEqual(resp, dict(some='json'))

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    def test_happy_path_post(self, http):
        http.return_value = StringIO('{"some": "json"}')
        resp = _get_json_for_fragment(
            {'node-url': 'http://localhost:1234/'},
            '/fragment/',
            method='POST',
            post_args={'foo': 'bar'}
        )
        self.assertEqual(resp, dict(some='json'))

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    def test_happy_path_for_cap(self, http):
        http.return_value = StringIO('{"some": "json"}')
        resp = _get_json_for_cap({'node-url': 'http://localhost:1234'}, 'fake cap')
        self.assertEqual(resp, dict(some='json'))

    @mock.patch('allmydata.scripts.tahoe_status.do_http')
    def test_no_data_returned(self, http):
        http.return_value = StringIO('null')

        with self.assertRaises(RuntimeError) as ctx:
            _get_json_for_cap({'node-url': 'http://localhost:1234'}, 'fake cap')
        self.assertTrue('No data from' in str(ctx.exception))

    def test_no_post_args(self):
        with self.assertRaises(ValueError) as ctx:
            _get_json_for_fragment(
                {'node-url': 'http://localhost:1234'},
                '/fragment',
                method='POST',
                post_args=None,
            )
        self.assertTrue(
            "Must pass post_args" in str(ctx.exception)
        )

    def test_post_args_for_get(self):
        with self.assertRaises(ValueError) as ctx:
            _get_json_for_fragment(
                {'node-url': 'http://localhost:1234'},
                '/fragment',
                method='GET',
                post_args={'foo': 'bar'}
            )
        self.assertTrue(
            "only valid for POST" in str(ctx.exception)
        )
