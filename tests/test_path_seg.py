from fcctl._async.pilot import seg


def test_seg_plain_unchanged():
    assert seg("site.example.com") == "site.example.com"
    assert seg("20261118_101010") == "20261118_101010"


def test_seg_quotes_slash_and_space():
    assert seg("a/b c") == "a%2Fb%20c"
