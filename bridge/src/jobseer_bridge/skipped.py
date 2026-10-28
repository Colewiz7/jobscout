# coverage test allows these gaps
SKIPPED = {
    "/api/v1/activity/": "activity feed is not exposed",
    "/dismiss": "activity feed is not exposed",
    "/api/v1/job-tags/undo": "retag with remove instead",
    "/api/v1/posting-edits/": "edit the posting again instead",
    "/api/v1/rule-actions/": "rule archive undo is a browser convenience",
    "/api/v1/profile/copy": "copy bookkeeping for the browser panel",
    "/description": "job_edit saves pasted posting text with undo support",
    "/overview": "summary worker routes are out of scope",
    "/overview/": "summary worker routes are out of scope",
    "/regenerate": "summary worker routes are out of scope",
    "/regenerate/undo": "summary worker routes are out of scope",
}
