"""SQL shared by the video lists of a channel, a studio, a tag and a performer.

The four name lists of every video (performers, local performers, studios, tags) used to be produced by joining all four tables to the
video in one statement and grouping by video. That multiplies the rows: a video with 113 performers, 26 studios and 3 tags produced
about 8,000 rows before the grouping, and 18 such videos took 125 ms. Fetching each list with its own sub-query gives the same lists, in
the same order, in 2.4 ms (tools/tests/test_video_lists_sql.py compares the two on random data).
"""


def name_lists_sql(performers: str = "performers", studios: str = "studios", tags: str = "tags", local_performers: str = "local_performers") -> str:
    """Four correlated sub-queries, one column each, for a query whose videos are aliased `m`."""
    return (
        "(SELECT GROUP_CONCAT(DISTINCT sp.name) FROM video_performers svp JOIN performers sp ON sp.id=svp.performer_id WHERE svp.video_id=m.id) "
        f"AS {performers}, "
        "(SELECT GROUP_CONCAT(DISTINCT slp.name) FROM local_video_performers slvp JOIN performers slp ON slp.id=slvp.performer_id WHERE slvp.video_id=m.id) "
        f"AS {local_performers}, "
        "(SELECT GROUP_CONCAT(DISTINCT ss.name) FROM video_studios svs JOIN studios ss ON ss.id=svs.studio_id WHERE svs.video_id=m.id) "
        f"AS {studios}, "
        "(SELECT GROUP_CONCAT(DISTINCT st.name) FROM video_tags svt JOIN tags st ON st.id=svt.tag_id WHERE svt.video_id=m.id) "
        f"AS {tags}"
    )
