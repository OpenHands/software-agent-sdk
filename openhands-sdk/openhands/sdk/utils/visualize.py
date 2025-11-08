from rich.text import Text


def display_dict(d) -> Text:
    """Create a Rich Text representation of a dictionary."""
    content = Text()
    for field_name, field_value in d.items():
        if field_value is None:
            continue  # skip None fields
        content.append(f"\n  {field_name}: ", style="bold")
        if isinstance(field_value, str):
            # Handle multiline strings with proper indentation
            if "\n" in field_value:
                content.append("\n")
                for line in field_value.split("\n"):
                    content.append(f"    {line}\n")
            else:
                content.append(f'"{field_value}"')
        elif isinstance(field_value, (list, dict)):
            content.append(str(field_value))
        else:
            content.append(str(field_value))
    return content
