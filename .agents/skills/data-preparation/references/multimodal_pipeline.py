"""Configuration-only DataFlow image-labeling pipeline template.

Copy this file into ``public_data/data-preparation/`` and customize ``CONFIG``.
The execution tools stage ``image_utils.py`` beside it locally and add the
revision-specific runtime directory to ``PYTHONPATH`` on Pyromind.
"""

from image_utils import ImagePipelineConfig, run_image_pipeline_from_cli


CONFIG = ImagePipelineConfig(
    labeling_system_prompt=(
        "Describe the image-labeling task, image roles, judgment guidance, "
        "and required JSON fields."
    ),
    training_system_prompt="You are a helpful assistant.",
    user_prompt_key="user_prompt",
    user_prompt_template=None,
    response_json_schema={
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "answer": {"type": "string"},
        },
        "required": ["reasoning", "answer"],
        "additionalProperties": False,
    },
    reasoning_key="reasoning",
    answer_key="answer",
)


if __name__ == "__main__":
    run_image_pipeline_from_cli(CONFIG)
