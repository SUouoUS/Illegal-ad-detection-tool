from clearweb.crawl import Policy, low_value_reason, strip_tracking, unsafe_reason


def test_unsafe_links_are_blocked():
    for u in ["http://a.go.kr/logout", "http://a.go.kr/member/logout.do", "http://a.go.kr/board/delete?id=3",
              "http://a.go.kr/bbs/boardDelete.do?no=1", "http://a.go.kr/bbs?mode=del&id=2", "http://a.go.kr/user/withdraw"]:
        assert unsafe_reason(u), u


def test_low_value_links():
    for u in ["http://a.go.kr/member/login.do", "http://a.go.kr/join", "http://a.go.kr/bbs/boardWrite.do",
              "http://a.go.kr/search?q=x", "http://a.go.kr/bbs?mode=write", "http://a.go.kr/bbs/recommend?no=1",
              "http://a.go.kr/common/fileDown.do?id=3", "http://a.go.kr/print.do?id=1", "http://a.go.kr/bbs?act=report&no=2"]:
        assert low_value_reason(u), u


def test_normal_links_are_allowed():
    for u in ["http://a.go.kr/board/view?id=3", "http://a.go.kr/model/list", "http://a.go.kr/delivery/info",
              "http://a.go.kr/board/list?page=2&searchCnd=&searchWrd=", "http://a.go.kr/about/outline",
              "http://a.go.kr/editorial/list", "http://a.go.kr/countdown"]:
        assert unsafe_reason(u) is None and low_value_reason(u) is None, u


def test_only_tracking_params_are_dropped():
    assert strip_tracking("http://a.kr/v?id=3&utm_source=x&fbclid=y") == "http://a.kr/v?id=3"
    assert strip_tracking("http://a.kr/v?id=3&t=cv&ref=main") == "http://a.kr/v?id=3&t=cv&ref=main"


def test_scope_files_and_duplicates():
    p = Policy("https://www.a.go.kr/")
    assert p.admit("https://www.a.go.kr/board/list?page=1", 1)
    assert p.admit("https://www.a.go.kr/board/list?page=1#top", 1) is None      # same page (fragment)
    assert p.admit("https://www.a.go.kr/board/list?utm_source=x&page=1", 1) is None  # tracking only
    assert p.admit("https://www.a.go.kr/board/list?page=2", 1)                   # query value differs
    assert p.admit("https://sub.a.go.kr/x", 1)                                   # subdomain in scope
    assert p.admit("https://other.kr/x", 1) is None
    assert p.admit("https://other.kr/x", 1) is None                               # counted once
    assert p.admit("https://www.a.go.kr/files/guide.pdf", 1) is None
    assert p.admit("mailto:x@a.go.kr", 1) is None
    assert p.skipped_n["범위 밖(다른 사이트)"] == 1 and p.skipped_n["파일 다운로드 링크"] == 1


def test_query_order_is_same_page():
    p = Policy("https://a.go.kr/")
    assert p.admit("https://a.go.kr/v?id=1&name=t", 1)
    assert p.admit("https://a.go.kr/v?name=t&id=1", 1) is None


def test_depth_and_pattern_limits():
    q = Policy("https://a.go.kr/", max_depth=2)
    assert q.admit("https://a.go.kr/deep", 3) is None and q.skipped_n["깊이 제한"] == 1
    assert q.admit("https://a.go.kr/deep", 3) is None and q.skipped_n["깊이 제한"] == 1   # counted once
    assert q.admit("https://a.go.kr/deep", 2)                                            # found shallower later
    r = Policy("https://a.go.kr/", per_pattern=2)
    assert r.admit("https://a.go.kr/cal?m=1", 1) and r.admit("https://a.go.kr/cal?m=2", 1)
    assert r.admit("https://a.go.kr/cal?m=3", 1) is None and r.skipped_n["같은 형태 주소 제한"] == 1
    # numeric path segments share one pattern
    assert Policy.pattern("https://a.go.kr/board/view/123") == Policy.pattern("https://a.go.kr/board/view/456")


def test_priority_prefers_shallow_main_content():
    p = Policy("https://a.go.kr/board/list")
    assert p.priority("https://a.go.kr/board/view?id=1", 1, "main") < p.priority("https://a.go.kr/about", 1, "nav")
    assert p.priority("https://a.go.kr/x", 1, "nav") < p.priority("https://a.go.kr/y", 2, "main")
    assert p.priority("https://a.go.kr/board/list?page=2", 1, "main") < p.priority("https://a.go.kr/board/list?page=9", 1, "main")


def test_pagination_is_lateral():
    p = Policy("https://a.go.kr/")
    assert p.child_depth("https://a.go.kr/list?page=1&bbs=3", "https://a.go.kr/list?page=2&bbs=3", 1) == 1
    assert p.child_depth("https://a.go.kr/list?page=1&bbs=3", "https://a.go.kr/list?page=2&bbs=4", 1) == 2
    assert p.child_depth("https://a.go.kr/list?page=1", "https://a.go.kr/view?id=2", 1) == 2
    assert p.child_depth("https://a.go.kr/list", "https://a.go.kr/list?pageIndex=2", 1) == 1


def test_near_duplicate_goes_last():
    p = Policy("https://a.go.kr/")
    assert p.admit("https://a.go.kr/view?id=3", 1)
    assert p.near_duplicate("https://a.go.kr/view?id=3&t=cv")
    assert not p.near_duplicate("https://a.go.kr/view?id=4")
    assert p.priority("https://a.go.kr/view?id=3&t=cv", 1, "main") > p.priority("https://a.go.kr/view?id=9", 1, "main")


def test_variant_found_first_is_superseded():
    p = Policy("https://a.go.kr/")
    assert p.admit("https://a.go.kr/view?id=3&t=cv", 1)
    assert not p.is_superseded("https://a.go.kr/view?id=3&t=cv")
    assert p.admit("https://a.go.kr/view?id=3", 1)
    assert p.is_superseded("https://a.go.kr/view?id=3&t=cv")


def test_two_variants_of_one_item():
    p = Policy("https://a.go.kr/view?id=3")
    assert p.near_duplicate("https://a.go.kr/view?id=3&t=cv")            # entry counts
    assert p.admit("https://a.go.kr/view?id=5&t=cv", 1)
    assert p.near_duplicate("https://a.go.kr/view?id=5&page=1")          # neither contains the other
    assert not p.near_duplicate("https://a.go.kr/view?id=6&page=1")      # different item


def test_list_pages_are_not_near_duplicates():
    p = Policy("https://a.go.kr/list?bbs=3")
    assert not p.near_duplicate("https://a.go.kr/list?bbs=3&page=2")
    assert p.near_duplicate("https://a.go.kr/list?bbs=3&page=1")         # page 1 = the same list


def test_account_words_in_any_query_value_are_low_value():
    """2026-10-03 museum: M4301000000.do?schM=mypage_update (login required) was visited
    because only action keys (mode=, act= ...) had their values checked."""
    from clearweb.crawl import low_value_reason
    assert low_value_reason("https://www.example.go.kr/MUSEUM/contents/M4301000000.do?schM=mypage_update") == "mypage"
    assert low_value_reason("https://www.example.go.kr/a.do?menu=loginForm") == "login"
    # broad words stay limited to action keys: report/view pages are content
    assert low_value_reason("https://www.example.go.kr/M0611000000.do?catCustomType=report&catId=126") is None
    assert low_value_reason("https://www.example.go.kr/board.do?type=report") == "report"   # action key, as before
