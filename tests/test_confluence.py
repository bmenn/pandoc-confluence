import os

import pytest

from pandoc_confluence import ConfluenceServer


@pytest.fixture
def url():
    return 'https://bmenn-dev.atlassian.net/wiki'

@pytest.fixture
def auth():
    return os.environ['CONFLUENCE_API_TOKEN'].split(':')

@pytest.fixture
def confluence_server(auth, url):
    return ConfluenceServer(auth, url)

# TODO Need clean up script
def test_write_read(confluence_server):
    confluence_server.upload('123', 'foobarbaz')
    page, _ = confluence_server.page('123')

    assert page == 'foobarbaz'
