{
  "ok": true,
  "counts": {
    "fail": 7,
    "blocked": 1,
    "pass": 131
  },
  "features_with_evidence": 139,
  "map_ids_without_evidence": 319,
  "changes": null,
  "conflicting": [
    {
      "feature": "S02.message-formatting",
      "attempts": [
        {
          "at": "2026-11-10T08:47:32+00:00",
          "result": "fail",
          "run": "S02"
        },
        {
          "at": "2026-11-10T08:55:59+00:00",
          "result": "pass",
          "run": "S02"
        }
      ]
    },
    {
      "feature": "S03.route-task-to-model",
      "attempts": [
        {
          "at": "2026-11-10T08:43:13+00:00",
          "result": "fail",
          "run": "S03"
        },
        {
          "at": "2026-11-10T08:53:26+00:00",
          "result": "pass",
          "run": "S03"
        }
      ]
    },
    {
      "feature": "S04.cancel-token",
      "attempts": [
        {
          "at": "2026-11-10T08:54:38+00:00",
          "result": "fail",
          "run": "S04"
        },
        {
          "at": "2026-11-10T08:59:30+00:00",
          "result": "pass",
          "run": "S04"
        }
      ]
    },
    {
      "feature": "S05.event-log-recovery",
      "attempts": [
        {
          "at": "2026-11-10T08:40:44+00:00",
          "result": "pass",
          "run": "S05"
        },
        {
          "at": "2026-11-10T08:51:45+00:00",
          "result": "pass",
          "run": "S05"
        },
        {
          "at": "2026-11-10T08:54:38+00:00",
          "result": "fail",
          "run": "S04"
        },
        {
          "at": "2026-11-10T08:59:30+00:00",
          "result": "fail",
          "run": "S04"
        }
      ]
    },
    {
      "feature": "S05.events-to-messages",
      "attempts": [
        {
          "at": "2026-11-10T08:40:44+00:00",
          "result": "fail",
          "run": "S05"
        },
        {
          "at": "2026-11-10T08:51:45+00:00",
          "result": "pass",
          "run": "S05"
        }
      ]
    },
    {
      "feature": "S05.json-roundtrip",
      "attempts": [
        {
          "at": "2026-11-10T08:40:44+00:00",
          "result": "fail",
          "run": "S05"
        },
        {
          "at": "2026-11-10T08:51:45+00:00",
          "result": "pass",
          "run": "S05"
        }
      ]
    },
    {
      "feature": "S05.openai-format",
      "attempts": [
        {
          "at": "2026-11-10T08:40:44+00:00",
          "result": "fail",
          "run": "S05"
        },
        {
          "at": "2026-11-10T08:51:45+00:00",
          "result": "pass",
          "run": "S05"
        }
      ]
    },
    {
      "feature": "S06.condenser-resume",
      "attempts": [
        {
          "at": "2026-11-10T08:46:04+00:00",
          "result": "fail",
          "run": "S06"
        },
        {
          "at": "2026-11-10T08:55:08+00:00",
          "result": "pass",
          "run": "S06"
        }
      ]
    },
    {
      "feature": "S06.summarizing-size",
      "attempts": [
        {
          "at": "2026-11-10T08:46:04+00:00",
          "result": "fail",
          "run": "S06"
        },
        {
          "at": "2026-11-10T08:55:08+00:00",
          "result": "pass",
          "run": "S06"
        }
      ]
    },
    {
      "feature": "S06.summary-llm-streaming",
      "attempts": [
        {
          "at": "2026-11-10T08:46:04+00:00",
          "result": "fail",
          "run": "S06"
        },
        {
          "at": "2026-11-10T08:55:08+00:00",
          "result": "pass",
          "run": "S06"
        }
      ]
    }
  ],
  "markdown": null,
  "out": null
}
