from pathlib import Path

from laminar.youtube import fetch_transcript_from_watch_url


def test_fetch_transcript_from_fixture_watch_page(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.xml"
    transcript_path.write_text(
        """
        <transcript>
          <text start="0" dur="1">hello world</text>
          <text start="1" dur="1">second line</text>
        </transcript>
        """
    )
    watch_path = tmp_path / "watch.html"
    watch_path.write_text(
        (
            'ytInitialPlayerResponse = {"captions":{"playerCaptionsTracklistRenderer":'
            '{"captionTracks":[{"baseUrl":"%s","languageCode":"en"}]}}};'
        )
        % transcript_path.as_uri()
    )

    transcript, language, source = fetch_transcript_from_watch_url(
        watch_path.as_uri(),
        ["en"],
    )

    assert transcript == "hello world\nsecond line"
    assert language == "en"
    assert source == "youtube_captions"
