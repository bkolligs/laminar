from laminar.youtube import extract_video_id, format_timestamp


def test_extract_video_id_from_urls() -> None:
    assert extract_video_id("https://www.youtube.com/watch?v=EBw7gsDPAYQ") == "EBw7gsDPAYQ"
    assert extract_video_id("https://youtu.be/EBw7gsDPAYQ") == "EBw7gsDPAYQ"


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(75) == "1:15"
    assert format_timestamp(3670) == "1:01:10"
