"""
Comprehensive tests for the env_parser module.

Tests cover:
- Basic environment parsers (bool, int, float, str, etc.)
- Complex parsers (list, dict, union, model parsers)
- Config class parsing with nested attributes and webhook specs
- Self-referential Node model parsing
- Enum and string literal parsing
- Template generation (to_env methods)
- Edge cases and error conditions
"""

import json
import os
from enum import Enum
from io import StringIO
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from openhands.agent_server.config import Config
from openhands.agent_server.env_parser import (
    MISSING,
    BoolEnvParser,
    DelayedParser,
    DictEnvParser,
    DiscriminatedUnionEnvParser,
    FloatEnvParser,
    IntEnvParser,
    ListEnvParser,
    LiteralEnvParser,
    ModelEnvParser,
    NoneEnvParser,
    StrEnvParser,
    UnionEnvParser,
    from_env,
    get_env_parser,
    merge,
    to_env,
)
from openhands.sdk.security.risk import SecurityRisk
from tests.sdk.utils.test_discriminated_union import Animal, Dog


class NodeModel(BaseModel):
    """Simple node model for testing basic recursive parsing."""

    name: str
    value: int = 0
    children: list["NodeModel"] = Field(default_factory=list)


class OptionalSubModel(BaseModel):
    title: str | None = None
    value: int | None = None


class OptionalModel(BaseModel):
    sub: OptionalSubModel | None = None


@pytest.fixture
def clean_env():
    """Clean environment fixture that removes test env vars after each test."""
    original_env = os.environ.copy()
    yield
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


def test_bool_env_parser(clean_env):
    """Test BoolEnvParser with various boolean representations."""
    parser = BoolEnvParser()

    # Test missing key
    assert parser.from_env("MISSING_KEY") is MISSING

    # Test truthy values
    for value in ["1", "TRUE", "true", "True"]:
        os.environ["TEST_BOOL"] = value
        assert parser.from_env("TEST_BOOL") is True
        del os.environ["TEST_BOOL"]

    # Test falsy values
    for value in ["0", "FALSE", "false", "False", ""]:
        os.environ["TEST_BOOL"] = value
        assert parser.from_env("TEST_BOOL") is False
        del os.environ["TEST_BOOL"]


def test_int_env_parser(clean_env):
    """Test IntEnvParser with various integer values."""
    parser = IntEnvParser()

    # Test missing key
    assert parser.from_env("MISSING_KEY") is MISSING

    # Test valid integers
    os.environ["TEST_INT"] = "42"
    assert parser.from_env("TEST_INT") == 42

    os.environ["TEST_INT"] = "-123"
    assert parser.from_env("TEST_INT") == -123

    os.environ["TEST_INT"] = "0"
    assert parser.from_env("TEST_INT") == 0

    # Test invalid integer
    os.environ["TEST_INT"] = "not_a_number"
    with pytest.raises(ValueError):
        parser.from_env("TEST_INT")


def test_float_env_parser(clean_env):
    """Test FloatEnvParser with various float values."""
    parser = FloatEnvParser()

    # Test missing key
    assert parser.from_env("MISSING_KEY") is MISSING

    # Test valid floats
    os.environ["TEST_FLOAT"] = "3.14"
    assert parser.from_env("TEST_FLOAT") == 3.14

    os.environ["TEST_FLOAT"] = "-2.5"
    assert parser.from_env("TEST_FLOAT") == -2.5

    os.environ["TEST_FLOAT"] = "0.0"
    assert parser.from_env("TEST_FLOAT") == 0.0

    # Test integer as float
    os.environ["TEST_FLOAT"] = "42"
    assert parser.from_env("TEST_FLOAT") == 42.0

    # Test invalid float
    os.environ["TEST_FLOAT"] = "not_a_number"
    with pytest.raises(ValueError):
        parser.from_env("TEST_FLOAT")


def test_str_env_parser(clean_env):
    """Test StrEnvParser with various string values."""
    parser = StrEnvParser()

    # Test missing key
    assert parser.from_env("MISSING_KEY") is MISSING

    # Test valid strings
    os.environ["TEST_STR"] = "hello world"
    assert parser.from_env("TEST_STR") == "hello world"

    os.environ["TEST_STR"] = ""
    assert parser.from_env("TEST_STR") == ""

    os.environ["TEST_STR"] = "123"
    assert parser.from_env("TEST_STR") == "123"


def test_none_env_parser(clean_env):
    """Test NoneEnvParser behavior."""
    parser = NoneEnvParser()

    # Test missing key (should return MISSING)
    assert parser.from_env("SOME_VALUE") is MISSING

    # Test present key (should return None)
    os.environ["SOME_VALUE_IS_NONE"] = "1"
    assert parser.from_env("SOME_VALUE") is None


def test_dict_env_parser(clean_env):
    """Test DictEnvParser with JSON dictionary values."""
    parser = DictEnvParser()

    # Test missing key
    assert parser.from_env("MISSING_KEY") is MISSING

    # Test valid JSON dict
    test_dict = {"key1": "value1", "key2": 42, "key3": True}
    os.environ["TEST_DICT"] = json.dumps(test_dict)
    result = parser.from_env("TEST_DICT")
    assert result == test_dict

    # Test empty dict
    os.environ["TEST_DICT"] = "{}"
    assert parser.from_env("TEST_DICT") == {}

    # Test invalid JSON
    os.environ["TEST_DICT"] = "not_json"
    with pytest.raises(json.JSONDecodeError):
        parser.from_env("TEST_DICT")

    # Test non-dict JSON
    os.environ["TEST_DICT"] = "[1, 2, 3]"
    with pytest.raises(AssertionError):
        parser.from_env("TEST_DICT")


def test_list_env_parser_with_json(clean_env):
    """Test ListEnvParser with JSON list values."""
    item_parser = StrEnvParser()
    parser = ListEnvParser(item_parser, str)

    # Test JSON list
    test_list = ["item1", "item2", "item3"]
    os.environ["TEST_LIST"] = json.dumps(test_list)
    result = parser.from_env("TEST_LIST")
    assert result == test_list

    # Test empty list
    os.environ["TEST_LIST"] = "[]"
    assert parser.from_env("TEST_LIST") == []

    # Test numeric list (indicating length)
    os.environ["TEST_LIST"] = "3"
    os.environ["TEST_LIST_0"] = "first"
    os.environ["TEST_LIST_1"] = "second"
    os.environ["TEST_LIST_2"] = "third"
    result = parser.from_env("TEST_LIST")
    assert result == ["first", "second", "third"]


def test_list_env_parser_sequential(clean_env):
    """Test ListEnvParser with sequential environment variables."""
    item_parser = StrEnvParser()
    parser = ListEnvParser(item_parser, str)

    # Test sequential items without base key
    os.environ["TEST_LIST_0"] = "first"
    os.environ["TEST_LIST_1"] = "second"
    os.environ["TEST_LIST_2"] = "third"
    result = parser.from_env("TEST_LIST")
    assert result == ["first", "second", "third"]

    # Test with gaps (should stop at first missing)
    del os.environ["TEST_LIST_1"]
    result = parser.from_env("TEST_LIST")
    assert result == ["first"]


def test_list_env_parser_with_complex_items(clean_env):
    """Test ListEnvParser with complex item types."""
    item_parser = IntEnvParser()
    parser = ListEnvParser(item_parser, int)

    # Test with integer items
    os.environ["TEST_LIST_0"] = "10"
    os.environ["TEST_LIST_1"] = "20"
    os.environ["TEST_LIST_2"] = "30"
    result = parser.from_env("TEST_LIST")
    assert result == [10, 20, 30]


def test_union_env_parser(clean_env):
    """Test UnionEnvParser with multiple parser types."""
    parsers = {str: StrEnvParser(), int: IntEnvParser()}
    parser = UnionEnvParser(parsers)

    # Test with string value that can't be parsed as int - this will fail
    os.environ["TEST_UNION"] = "hello"
    with pytest.raises(ValueError):
        parser.from_env("TEST_UNION")

    # Test with integer value (both parsers succeed, merge returns last)
    os.environ["TEST_UNION"] = "42"
    result = parser.from_env("TEST_UNION")
    # String parser returns "42", int parser returns 42, merge returns 42
    assert result == 42

    # Test with compatible parsers (str and bool)
    bool_str_parsers = {str: StrEnvParser(), bool: BoolEnvParser()}
    bool_str_parser = UnionEnvParser(bool_str_parsers)

    os.environ["TEST_UNION"] = "true"
    result = bool_str_parser.from_env("TEST_UNION")
    # String parser returns "true", bool parser returns True, merge returns True
    assert result is True


def test_model_env_parser_simple(clean_env):
    """Test ModelEnvParser with a simple model."""

    class SimpleModel(BaseModel):
        name: str = "default"
        count: int = 0

    field_parsers = {
        "name": StrEnvParser(),
        "count": IntEnvParser(),
    }
    descriptions = {}
    parser = ModelEnvParser(field_parsers, descriptions)

    # Test with individual field overrides
    os.environ["TEST_MODEL_NAME"] = "test_name"
    os.environ["TEST_MODEL_COUNT"] = "42"
    result = parser.from_env("TEST_MODEL")
    expected = {"name": "test_name", "count": 42}
    assert result == expected

    # Test with JSON base and field overrides
    del os.environ["TEST_MODEL_NAME"]  # Clear previous test
    base_data = {"name": "json_name", "count": 10}
    os.environ["TEST_MODEL"] = json.dumps(base_data)
    os.environ["TEST_MODEL_COUNT"] = "99"  # Override count
    result = parser.from_env("TEST_MODEL")
    expected = {"name": "json_name", "count": 99}
    assert result == expected


def test_delayed_parser(clean_env):
    """Test DelayedParser for handling circular dependencies."""
    delayed = DelayedParser()

    # Test without setting parser (should raise assertion error)
    with pytest.raises(AssertionError):
        delayed.from_env("TEST_KEY")

    # Test with parser set
    delayed.parser = StrEnvParser()
    os.environ["TEST_KEY"] = "test_value"
    assert delayed.from_env("TEST_KEY") == "test_value"


def test_merge_function():
    """Test the merge function with various data types."""
    # Test with MISSING values
    assert merge(MISSING, "value") == "value"
    assert merge("value", MISSING) == "value"
    assert merge(MISSING, MISSING) is MISSING

    # Test with simple values (later overwrites earlier)
    assert merge("old", "new") == "new"
    assert merge(1, 2) == 2

    # Test with dictionaries
    dict1 = {"a": 1, "b": 2}
    dict2 = {"b": 3, "c": 4}
    expected = {"a": 1, "b": 3, "c": 4}
    assert merge(dict1, dict2) == expected

    # Test with nested dictionaries
    dict1 = {"nested": {"a": 1, "b": 2}}
    dict2 = {"nested": {"b": 3, "c": 4}}
    expected = {"nested": {"a": 1, "b": 3, "c": 4}}
    assert merge(dict1, dict2) == expected

    # Test with lists
    list1 = [1, 2, 3]
    list2 = [10, 20]
    expected = [10, 20, 3]
    assert merge(list1, list2) == expected

    # Test with lists of different lengths (second list longer) - this will fail
    list1 = [1, 2]
    list2 = [10, 20, 30, 40]
    # The current implementation has a bug - it tries to assign to index that
    # doesn't exist
    with pytest.raises(IndexError):
        merge(list1, list2)

    # Test with lists of different lengths (first list longer)
    list1 = [1, 2, 3, 4]
    list2 = [10, 20]
    expected = [10, 20, 3, 4]
    assert merge(list1, list2) == expected


def test_get_env_parser_basic_types():
    """Test get_env_parser with basic types."""
    parsers = {
        str: StrEnvParser(),
        int: IntEnvParser(),
        float: FloatEnvParser(),
        bool: BoolEnvParser(),
        type(None): NoneEnvParser(),
    }

    # Test basic types
    assert isinstance(get_env_parser(str, parsers), StrEnvParser)
    assert isinstance(get_env_parser(int, parsers), IntEnvParser)
    assert isinstance(get_env_parser(float, parsers), FloatEnvParser)
    assert isinstance(get_env_parser(bool, parsers), BoolEnvParser)
    assert isinstance(get_env_parser(type(None), parsers), NoneEnvParser)


def test_get_env_parser_complex_types():
    """Test get_env_parser with complex types."""
    parsers = {
        str: StrEnvParser(),
        int: IntEnvParser(),
        float: FloatEnvParser(),
        bool: BoolEnvParser(),
        type(None): NoneEnvParser(),
    }

    # Test list type
    list_parser = get_env_parser(list[str], parsers)
    assert isinstance(list_parser, ListEnvParser)
    assert isinstance(list_parser.item_parser, StrEnvParser)

    # Test dict type
    dict_parser = get_env_parser(dict[str, str], parsers)
    assert isinstance(dict_parser, DictEnvParser)

    # Test union type
    union_parser = get_env_parser(str | int, parsers)  # type: ignore[arg-type]
    assert isinstance(union_parser, UnionEnvParser)
    assert len(union_parser.parsers) == 2


def test_get_env_parser_model_type():
    """Test get_env_parser with BaseModel types."""

    class TestModel(BaseModel):
        name: str
        value: int

    parsers = {
        str: StrEnvParser(),
        int: IntEnvParser(),
        float: FloatEnvParser(),
        bool: BoolEnvParser(),
        type(None): NoneEnvParser(),
    }
    model_parser = get_env_parser(TestModel, parsers)
    assert isinstance(model_parser, ModelEnvParser)
    assert "name" in model_parser.parsers
    assert "value" in model_parser.parsers
    assert isinstance(model_parser.parsers["name"], StrEnvParser)
    assert isinstance(model_parser.parsers["value"], IntEnvParser)


def test_config_class_parsing(clean_env):
    """Test parsing the Config class with nested attributes and webhook specs."""
    # Test basic config parsing
    os.environ["OH_SESSION_API_KEYS_0"] = "key1"
    os.environ["OH_SESSION_API_KEYS_1"] = "key2"
    os.environ["OH_ALLOW_CORS_ORIGINS_0"] = "http://localhost:3000"
    os.environ["OH_CONVERSATIONS_PATH"] = "/custom/conversations"
    os.environ["OH_ENABLE_VSCODE"] = "false"

    config = from_env(Config, "OH")

    assert config.session_api_keys == ["key1", "key2"]
    assert config.allow_cors_origins == ["http://localhost:3000"]
    assert config.conversations_path == Path("/custom/conversations")
    assert config.enable_vscode is False


def test_config_webhook_specs_parsing(clean_env):
    """Test parsing webhook specs in Config class."""
    # Test with JSON webhook specs
    webhook_data = [
        {
            "base_url": "https://webhook1.example.com",
            "headers": {"Authorization": "Bearer token1"},
            "event_buffer_size": 5,
            "flush_delay": 15.0,
            "num_retries": 2,
            "retry_delay": 3,
        },
        {
            "base_url": "https://webhook2.example.com",
            "headers": {"X-API-Key": "secret"},
            "event_buffer_size": 20,
            "flush_delay": 60.0,
        },
    ]
    os.environ["OH_WEBHOOKS"] = json.dumps(webhook_data)

    config = from_env(Config, "OH")

    assert len(config.webhooks) == 2
    assert config.webhooks[0].base_url == "https://webhook1.example.com"
    assert config.webhooks[0].headers == {"Authorization": "Bearer token1"}
    assert config.webhooks[0].event_buffer_size == 5
    assert config.webhooks[0].flush_delay == 15.0
    assert config.webhooks[0].num_retries == 2
    assert config.webhooks[0].retry_delay == 3

    assert config.webhooks[1].base_url == "https://webhook2.example.com"
    assert config.webhooks[1].headers == {"X-API-Key": "secret"}
    assert config.webhooks[1].event_buffer_size == 20
    assert config.webhooks[1].flush_delay == 60.0
    # Default values should be used
    assert config.webhooks[1].num_retries == 3
    assert config.webhooks[1].retry_delay == 5


def test_config_webhook_specs_sequential_parsing(clean_env):
    """Test parsing webhook specs using sequential environment variables."""
    # Test with sequential webhook environment variables
    os.environ["OH_WEBHOOKS_0_BASE_URL"] = "https://webhook1.example.com"
    os.environ["OH_WEBHOOKS_0_EVENT_BUFFER_SIZE"] = "15"
    os.environ["OH_WEBHOOKS_0_FLUSH_DELAY"] = "25.5"
    os.environ["OH_WEBHOOKS_0_HEADERS"] = json.dumps({"Auth": "token1"})

    os.environ["OH_WEBHOOKS_1_BASE_URL"] = "https://webhook2.example.com"
    os.environ["OH_WEBHOOKS_1_NUM_RETRIES"] = "5"
    os.environ["OH_WEBHOOKS_1_RETRY_DELAY"] = "10"

    config = from_env(Config, "OH")

    assert len(config.webhooks) == 2
    assert config.webhooks[0].base_url == "https://webhook1.example.com"
    assert config.webhooks[0].event_buffer_size == 15
    assert config.webhooks[0].flush_delay == 25.5
    assert config.webhooks[0].headers == {"Auth": "token1"}

    assert config.webhooks[1].base_url == "https://webhook2.example.com"
    assert config.webhooks[1].num_retries == 5
    assert config.webhooks[1].retry_delay == 10


def test_config_mixed_webhook_parsing(clean_env):
    """Test parsing webhooks with mixed JSON and individual overrides."""
    # Set base JSON with one webhook
    base_webhooks = [
        {
            "base_url": "https://base.example.com",
            "event_buffer_size": 10,
        }
    ]
    os.environ["OH_WEBHOOKS"] = json.dumps(base_webhooks)

    # Override specific fields
    os.environ["OH_WEBHOOKS_0_FLUSH_DELAY"] = "45.0"
    os.environ["OH_WEBHOOKS_0_HEADERS"] = json.dumps({"Override": "header"})

    config = from_env(Config, "OH")

    assert len(config.webhooks) == 1
    # First webhook: base + overrides
    assert config.webhooks[0].base_url == "https://base.example.com"
    assert config.webhooks[0].event_buffer_size == 10
    assert config.webhooks[0].flush_delay == 45.0
    assert config.webhooks[0].headers == {"Override": "header"}


def test_node_model_parsing(clean_env):
    """Test parsing a simple node model."""
    # Test simple node
    os.environ["TEST_NODE_NAME"] = "root"
    os.environ["TEST_NODE_VALUE"] = "42"

    node = from_env(NodeModel, "TEST_NODE")
    assert node.name == "root"
    assert node.value == 42


def test_node_model_parsing_with_recursion(clean_env):
    """Test parsing a simple node model."""
    # Test simple node
    os.environ["TEST_NODE_NAME"] = "root"
    os.environ["TEST_NODE_VALUE"] = "42"
    os.environ["TEST_NODE_CHILDREN_0_NAME"] = "child 1"
    os.environ["TEST_NODE_CHILDREN_1_NAME"] = "child 2"

    node = from_env(NodeModel, "TEST_NODE")
    assert node.name == "root"
    assert node.value == 42
    expected_children = [
        NodeModel(name="child 1"),
        NodeModel(name="child 2"),
    ]
    assert node.children == expected_children


def test_node_model_with_json(clean_env):
    """Test parsing SimpleNode model with JSON."""
    node_data = {
        "name": "json_node",
        "value": 100,
    }
    os.environ["TEST_NODE"] = json.dumps(node_data)

    node = from_env(NodeModel, "TEST_NODE")
    assert node.name == "json_node"
    assert node.value == 100


def test_node_model_mixed_parsing(clean_env):
    """Test parsing SimpleNode model with mixed JSON and env overrides."""
    # Base JSON structure
    base_data = {
        "name": "base_name",
        "value": 10,
    }
    os.environ["TEST_NODE"] = json.dumps(base_data)

    # Override value
    os.environ["TEST_NODE_VALUE"] = "99"

    node = from_env(NodeModel, "TEST_NODE")
    assert node.name == "base_name"
    assert node.value == 99


def test_from_env_with_defaults(clean_env):
    """Test from_env function with default values when no env vars are set."""

    class DefaultModel(BaseModel):
        name: str = "default_name"
        count: int = 42
        enabled: bool = True

    # No environment variables set
    result = from_env(DefaultModel, "TEST")
    assert result.name == "default_name"
    assert result.count == 42
    assert result.enabled is True


def test_from_env_with_custom_parsers(clean_env):
    """Test from_env function with custom parser overrides."""

    class CustomModel(BaseModel):
        value: str

    # Custom parser that always returns "custom"
    class CustomStrParser:
        def from_env(self, key: str):
            return "custom"

    custom_parsers = {str: CustomStrParser()}  # type: ignore[dict-item]
    os.environ["TEST_VALUE"] = "ignored"

    result = from_env(CustomModel, "TEST", custom_parsers)  # type: ignore[arg-type]
    assert result.value == "custom"


def test_error_handling_invalid_json(clean_env):
    """Test error handling with invalid JSON in environment variables."""

    class TestModel(BaseModel):
        data: dict[str, str]

    os.environ["TEST_DATA"] = "invalid_json"

    with pytest.raises(json.JSONDecodeError):
        from_env(TestModel, "TEST")


def test_error_handling_unknown_type():
    """Test error handling with unknown types."""

    class UnknownType:
        pass

    parsers = {}
    with pytest.raises(ValueError, match="unknown_type"):
        get_env_parser(UnknownType, parsers)


def test_optional_fields_parsing(clean_env):
    """Test parsing models with optional fields."""

    class OptionalModel(BaseModel):
        required_field: str
        optional_field: str | None = None
        optional_with_default: str = "default"

    os.environ["TEST_REQUIRED_FIELD"] = "required_value"
    # Don't set optional fields

    result = from_env(OptionalModel, "TEST")
    assert result.required_field == "required_value"
    assert result.optional_field is None
    assert result.optional_with_default == "default"

    # Now set optional field
    os.environ["TEST_OPTIONAL_FIELD"] = "optional_value"
    result = from_env(OptionalModel, "TEST")
    assert result.optional_field == "optional_value"


def test_complex_nested_structure(clean_env):
    """Test parsing complex nested structures."""

    class Address(BaseModel):
        street: str
        city: str
        zip_code: str

    class Person(BaseModel):
        name: str
        age: int
        addresses: list[Address]

    # Set up complex nested data
    person_data = {
        "name": "John Doe",
        "age": 30,
        "addresses": [
            {"street": "123 Main St", "city": "Anytown", "zip_code": "12345"},
            {"street": "456 Oak Ave", "city": "Other City", "zip_code": "67890"},
        ],
    }
    os.environ["TEST_PERSON"] = json.dumps(person_data)

    # Override some nested values
    os.environ["TEST_PERSON_AGE"] = "35"
    os.environ["TEST_PERSON_ADDRESSES_0_CITY"] = "New City"
    os.environ["TEST_PERSON_ADDRESSES_1_ZIP_CODE"] = "99999"

    result = from_env(Person, "TEST_PERSON")
    assert result.name == "John Doe"
    assert result.age == 35  # Overridden
    assert len(result.addresses) == 2

    assert result.addresses[0].street == "123 Main St"
    assert result.addresses[0].city == "New City"  # Overridden
    assert result.addresses[0].zip_code == "12345"

    assert result.addresses[1].street == "456 Oak Ave"
    assert result.addresses[1].city == "Other City"
    assert result.addresses[1].zip_code == "99999"  # Overridden


def test_optional_parameter_parsing(clean_env):
    os.environ["OP_SUB_TITLE"] = "Present"
    os.environ["OP_SUB_VALUE"] = "10"
    model = from_env(OptionalModel, "OP")
    assert model == OptionalModel(sub=OptionalSubModel(title="Present", value=10))


def test_discriminated_union_parsing(clean_env):
    os.environ["A_KIND"] = "Dog"
    os.environ["A_NAME"] = "Bowser"
    os.environ["A_BARKING"] = "1"
    model = from_env(Animal, "A")
    assert model == Dog(name="Bowser", barking=True)


def test_config_vnc_environment_variable_parsing(clean_env):
    """Test parsing OH_ENABLE_VNC environment variable in Config class."""
    # Test OH_ENABLE_VNC set to true
    os.environ["OH_ENABLE_VNC"] = "true"
    config = from_env(Config, "OH")
    assert config.enable_vnc is True

    # Test OH_ENABLE_VNC set to false
    os.environ["OH_ENABLE_VNC"] = "false"
    config = from_env(Config, "OH")
    assert config.enable_vnc is False

    # Test default value when OH_ENABLE_VNC is not set
    del os.environ["OH_ENABLE_VNC"]
    config = from_env(Config, "OH")
    assert config.enable_vnc is False  # Default value from Config class


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        ("", False),
    ],
)
def test_config_vnc_various_boolean_values(clean_env, env_value, expected):
    """Test that OH_ENABLE_VNC accepts various boolean representations."""
    os.environ["OH_ENABLE_VNC"] = env_value
    config = from_env(Config, "OH")
    assert config.enable_vnc is expected, (
        f"Failed for OH_ENABLE_VNC='{env_value}', expected {expected}"
    )


# ============================================================================
# ENUM PARSING TESTS
# ============================================================================


class SampleEnum(str, Enum):
    """Sample enum for parsing tests."""

    OPTION_A = "option_a"
    OPTION_B = "option_b"
    OPTION_C = "option_c"


def test_enum_env_parser_creation():
    """Test that enum types create LiteralEnvParser with correct values."""
    parsers = {}
    parser = get_env_parser(SampleEnum, parsers)

    assert isinstance(parser, LiteralEnvParser)
    assert parser.values == ("option_a", "option_b", "option_c")


def test_enum_parsing_valid_values(clean_env):
    """Test parsing valid enum values from environment variables."""

    class EnumModel(BaseModel):
        risk_level: SecurityRisk = SecurityRisk.LOW
        test_option: SampleEnum = SampleEnum.OPTION_A

    # Test SecurityRisk enum
    os.environ["TEST_RISK_LEVEL"] = "HIGH"
    os.environ["TEST_TEST_OPTION"] = "option_b"

    result = from_env(EnumModel, "TEST")
    assert result.risk_level == SecurityRisk.HIGH
    assert result.test_option == SampleEnum.OPTION_B


def test_enum_parsing_invalid_values(clean_env):
    """Test parsing invalid enum values from environment variables."""

    class EnumModel(BaseModel):
        risk_level: SecurityRisk = SecurityRisk.LOW

    # Test invalid enum value
    os.environ["TEST_RISK_LEVEL"] = "INVALID_RISK"

    # Should use default value when invalid value is provided
    result = from_env(EnumModel, "TEST")
    assert result.risk_level == SecurityRisk.LOW


def test_enum_parsing_missing_values(clean_env):
    """Test parsing when enum environment variables are missing."""

    class EnumModel(BaseModel):
        risk_level: SecurityRisk = SecurityRisk.MEDIUM
        test_option: SampleEnum = SampleEnum.OPTION_C

    # No environment variables set - should use defaults
    result = from_env(EnumModel, "TEST")
    assert result.risk_level == SecurityRisk.MEDIUM
    assert result.test_option == SampleEnum.OPTION_C


# ============================================================================
# STRING LITERAL PARSING TESTS
# ============================================================================


def test_literal_env_parser_creation():
    """Test that Literal types create LiteralEnvParser with correct values."""
    type_: type = Literal["red", "green", "blue"]  # type: ignore
    parsers = {}
    parser = get_env_parser(type_, parsers)

    assert isinstance(parser, LiteralEnvParser)
    assert parser.values == ("red", "green", "blue")


def test_literal_parsing_valid_values(clean_env):
    """Test parsing valid literal values from environment variables."""

    class LiteralModel(BaseModel):
        color: Literal["red", "green", "blue"] = "red"
        size: Literal["small", "medium", "large"] = "medium"

    os.environ["TEST_COLOR"] = "blue"
    os.environ["TEST_SIZE"] = "large"

    result = from_env(LiteralModel, "TEST")
    assert result.color == "blue"
    assert result.size == "large"


def test_literal_parsing_invalid_values(clean_env):
    """Test parsing invalid literal values from environment variables."""

    class LiteralModel(BaseModel):
        color: Literal["red", "green", "blue"] = "red"

    # Test invalid literal value
    os.environ["TEST_COLOR"] = "purple"

    # Should use default value when invalid value is provided
    result = from_env(LiteralModel, "TEST")
    assert result.color == "red"


def test_literal_parsing_missing_values(clean_env):
    """Test parsing when literal environment variables are missing."""

    class LiteralModel(BaseModel):
        color: Literal["red", "green", "blue"] = "green"
        size: Literal["small", "medium", "large"] = "small"

    # No environment variables set - should use defaults
    result = from_env(LiteralModel, "TEST")
    assert result.color == "green"
    assert result.size == "small"


def test_literal_env_parser_direct():
    """Test LiteralEnvParser directly with various scenarios."""
    parser = LiteralEnvParser(("alpha", "beta", "gamma"))

    # Test missing key
    assert parser.from_env("MISSING_KEY") is MISSING

    # Test valid values
    os.environ["TEST_LITERAL"] = "alpha"
    assert parser.from_env("TEST_LITERAL") == "alpha"

    os.environ["TEST_LITERAL"] = "beta"
    assert parser.from_env("TEST_LITERAL") == "beta"

    # Test invalid value
    os.environ["TEST_LITERAL"] = "invalid"
    assert parser.from_env("TEST_LITERAL") is MISSING

    # Clean up
    del os.environ["TEST_LITERAL"]


# ============================================================================
# TEMPLATE GENERATION (to_env) TESTS
# ============================================================================


def test_bool_env_parser_to_env():
    """Test BoolEnvParser template generation."""
    parser = BoolEnvParser()
    output = StringIO()

    # Test True value
    parser.to_env("TEST_BOOL", True, output)
    assert output.getvalue() == "TEST_BOOL=1\n"

    # Test False value
    output = StringIO()
    parser.to_env("TEST_BOOL", False, output)
    assert output.getvalue() == "TEST_BOOL=0\n"


def test_none_env_parser_to_env():
    """Test NoneEnvParser template generation."""
    parser = NoneEnvParser()
    output = StringIO()

    # Test None value
    parser.to_env("TEST_VALUE", None, output)
    assert output.getvalue() == "TEST_VALUE_IS_NONE=1\n"

    # Test non-None value (should produce no output)
    output = StringIO()
    parser.to_env("TEST_VALUE", "not_none", output)
    assert output.getvalue() == ""


def test_literal_env_parser_to_env():
    """Test LiteralEnvParser template generation."""
    parser = LiteralEnvParser(("red", "green", "blue"))
    output = StringIO()

    parser.to_env("TEST_COLOR", "red", output)
    result = output.getvalue()

    # Should include permitted values comment and the actual value
    assert "# Permitted Values: red, green, blue" in result
    assert "TEST_COLOR=red\n" in result


def test_list_env_parser_to_env():
    """Test ListEnvParser template generation."""
    item_parser = StrEnvParser()
    parser = ListEnvParser(item_parser, str)
    output = StringIO()

    test_list = ["item1", "item2", "item3"]
    parser.to_env("TEST_LIST", test_list, output)
    result = output.getvalue()

    # Should generate indexed environment variables
    assert "TEST_LIST_0=item1\n" in result
    assert "TEST_LIST_1=item2\n" in result
    assert "TEST_LIST_2=item3\n" in result


def test_model_env_parser_to_env():
    """Test ModelEnvParser template generation."""

    class TestModel(BaseModel):
        name: str = Field(description="The name field")
        count: int = Field(description="The count field")
        enabled: bool = True

    # Create model instance
    model = TestModel(name="test", count=42, enabled=False)

    # Generate template
    template = to_env(model, "TEST_MODEL")

    # Should include field descriptions and values
    assert "# The name field" in template
    assert "# The count field" in template
    assert "TEST_MODEL_NAME=test" in template
    assert "TEST_MODEL_COUNT=42" in template
    assert "TEST_MODEL_ENABLED=0" in template


def test_union_env_parser_to_env():
    """Test UnionEnvParser template generation."""
    parsers = {str: StrEnvParser(), int: IntEnvParser()}
    parser = UnionEnvParser(parsers)
    output = StringIO()

    # Test with string value
    parser.to_env("TEST_UNION", "hello", output)
    result = output.getvalue()

    # Should include the actual value and commented samples
    assert "TEST_UNION=hello\n" in result


def test_to_env_function_with_enum():
    """Test the main to_env function with enum values."""

    class EnumModel(BaseModel):
        risk: SecurityRisk = SecurityRisk.LOW
        option: SampleEnum = SampleEnum.OPTION_A

    model = EnumModel(risk=SecurityRisk.HIGH, option=SampleEnum.OPTION_B)
    template = to_env(model, "TEST")

    # Should generate templates for enum fields
    assert "TEST_RISK=HIGH" in template
    assert "TEST_OPTION=option_b" in template
    # Should include permitted values comments
    assert "Permitted Values:" in template


def test_to_env_function_with_literal():
    """Test the main to_env function with literal values."""

    class LiteralModel(BaseModel):
        color: Literal["red", "green", "blue"] = "red"
        size: Literal["small", "medium", "large"] = "medium"

    model = LiteralModel(color="blue", size="large")
    template = to_env(model, "TEST")

    # Should generate templates for literal fields
    assert "TEST_COLOR=blue" in template
    assert "TEST_SIZE=large" in template
    # Should include permitted values comments
    assert "Permitted Values:" in template


def test_to_env_function_with_complex_model():
    """Test the main to_env function with a complex nested model."""

    class Address(BaseModel):
        street: str = Field(description="Street address")
        city: str = Field(description="City name")
        zip_code: str = "00000"

    class Person(BaseModel):
        name: str = Field(description="Person's name")
        age: int = Field(description="Person's age")
        addresses: list[Address] = Field(
            default_factory=list, description="List of addresses"
        )
        risk_level: SecurityRisk = SecurityRisk.LOW

    # Create complex model instance
    person = Person(
        name="John Doe",
        age=30,
        addresses=[
            Address(street="123 Main St", city="Anytown", zip_code="12345"),
            Address(street="456 Oak Ave", city="Other City", zip_code="67890"),
        ],
        risk_level=SecurityRisk.MEDIUM,
    )

    template = to_env(person, "PERSON")

    # Should include field descriptions
    assert "# Person's name" in template
    assert "# Person's age" in template
    assert "# List of addresses" in template
    assert "# Street address" in template
    assert "# City name" in template

    # Should include nested structure
    assert "PERSON_NAME=John Doe" in template
    assert "PERSON_AGE=30" in template
    assert "PERSON_ADDRESSES_0_STREET=123 Main St" in template
    assert "PERSON_ADDRESSES_0_CITY=Anytown" in template
    assert "PERSON_ADDRESSES_0_ZIP_CODE=12345" in template
    assert "PERSON_ADDRESSES_1_STREET=456 Oak Ave" in template
    assert "PERSON_ADDRESSES_1_CITY=Other City" in template
    assert "PERSON_ADDRESSES_1_ZIP_CODE=67890" in template
    assert "PERSON_RISK_LEVEL=MEDIUM" in template


def test_to_env_function_with_none_values():
    """Test the main to_env function with None values."""

    class OptionalModel(BaseModel):
        required_field: str
        optional_field: str | None = None
        another_optional: int | None = None

    model = OptionalModel(
        required_field="required", optional_field=None, another_optional=42
    )

    template = to_env(model, "TEST")

    # Should handle None values with _IS_NONE suffix
    assert "TEST_REQUIRED_FIELD=required" in template
    assert "TEST_OPTIONAL_FIELD_IS_NONE=1" in template
    assert "TEST_ANOTHER_OPTIONAL=42" in template


def test_to_env_function_with_boolean_values():
    """Test the main to_env function with boolean values."""

    class BoolModel(BaseModel):
        enabled: bool = True
        disabled: bool = False
        maybe: bool | None = None

    model = BoolModel(enabled=True, disabled=False, maybe=None)
    template = to_env(model, "BOOL_TEST")

    # Should convert booleans to 1/0
    assert "BOOL_TEST_ENABLED=1" in template
    assert "BOOL_TEST_DISABLED=0" in template
    assert "BOOL_TEST_MAYBE_IS_NONE=1" in template


# ============================================================================
# DISCRIMINATED UNION ENV PARSER TESTS
# ============================================================================


def test_discriminated_union_single_kind_uses_parser_directly(clean_env):
    """Test that DiscriminatedUnionEnvParser uses the parser directly when there's
    only one kind."""
    # Create a single parser
    single_parser = ModelEnvParser(
        parsers={"name": StrEnvParser(), "barking": BoolEnvParser()},
        descriptions={},
    )
    parser = DiscriminatedUnionEnvParser(parsers={"Dog": single_parser})

    # Set up environment without KIND
    os.environ["TEST_NAME"] = "Fido"
    os.environ["TEST_BARKING"] = "1"

    # Should use the single parser directly without requiring KIND
    result = parser.from_env("TEST")
    assert result == {"name": "Fido", "barking": True, "kind": "Dog"}


def test_discriminated_union_multiple_kinds_requires_kind(clean_env):
    """Test that DiscriminatedUnionEnvParser returns MISSING when there are multiple
    kinds and no KIND is set."""
    # Create multiple parsers
    dog_parser = ModelEnvParser(
        parsers={"name": StrEnvParser(), "barking": BoolEnvParser()},
        descriptions={},
    )
    cat_parser = ModelEnvParser(
        parsers={"name": StrEnvParser()},
        descriptions={},
    )
    parser = DiscriminatedUnionEnvParser(parsers={"Dog": dog_parser, "Cat": cat_parser})

    # Set up environment without KIND
    os.environ["TEST_NAME"] = "Fido"
    os.environ["TEST_BARKING"] = "1"

    # Should return MISSING because there are multiple kinds and no KIND is set
    result = parser.from_env("TEST")
    assert result is MISSING


def test_discriminated_union_multiple_kinds_with_kind_set(clean_env):
    """Test that DiscriminatedUnionEnvParser works correctly when KIND is
    explicitly set."""
    # Create multiple parsers
    dog_parser = ModelEnvParser(
        parsers={"name": StrEnvParser(), "barking": BoolEnvParser()},
        descriptions={},
    )
    cat_parser = ModelEnvParser(
        parsers={"name": StrEnvParser()},
        descriptions={},
    )
    parser = DiscriminatedUnionEnvParser(parsers={"Dog": dog_parser, "Cat": cat_parser})

    # Set up environment with KIND
    os.environ["TEST_KIND"] = "Dog"
    os.environ["TEST_NAME"] = "Fido"
    os.environ["TEST_BARKING"] = "1"

    result = parser.from_env("TEST")
    assert result == {"name": "Fido", "barking": True, "kind": "Dog"}


def test_discriminated_union_zero_kinds_returns_missing(clean_env):
    """Test that DiscriminatedUnionEnvParser returns MISSING when there are no kinds."""
    parser = DiscriminatedUnionEnvParser(parsers={})

    os.environ["TEST_NAME"] = "Fido"

    # Should return MISSING because there are no parsers
    result = parser.from_env("TEST")
    assert result is MISSING


def test_discriminated_union_full_class_name_imports_and_registers(clean_env):
    """Test that DiscriminatedUnionEnvParser handles full class names with dots."""
    # Start with an empty parser
    parser = DiscriminatedUnionEnvParser(parsers={})

    # Set KIND to a full class name (using the test Dog class)
    os.environ["TEST_KIND"] = "tests.sdk.utils.test_discriminated_union.Dog"
    os.environ["TEST_NAME"] = "Fido"
    os.environ["TEST_BARKING"] = "1"

    result = parser.from_env("TEST")

    # Should import the class, create a parser, and return the data
    assert result == {"name": "Fido", "barking": True, "kind": "Dog"}
    # Parser should now be registered with the unqualified class name
    assert "Dog" in parser.parsers


def test_discriminated_union_full_class_name_already_registered(clean_env):
    """Test that full class names work when class is already registered."""
    # Pre-register a Dog parser
    dog_parser = ModelEnvParser(
        parsers={"name": StrEnvParser(), "barking": BoolEnvParser()},
        descriptions={},
    )
    parser = DiscriminatedUnionEnvParser(parsers={"Dog": dog_parser})

    # Set KIND to a full class name for the already registered class
    os.environ["TEST_KIND"] = "tests.sdk.utils.test_discriminated_union.Dog"
    os.environ["TEST_NAME"] = "Rex"
    os.environ["TEST_BARKING"] = "0"

    result = parser.from_env("TEST")

    # Should use the existing parser (not re-import)
    assert result == {"name": "Rex", "barking": False, "kind": "Dog"}


def test_discriminated_union_full_class_name_different_classes(clean_env):
    """Test that multiple full class names can be used to import different classes."""
    parser = DiscriminatedUnionEnvParser(parsers={})

    # First, import Dog using full class name
    os.environ["TEST_KIND"] = "tests.sdk.utils.test_discriminated_union.Dog"
    os.environ["TEST_NAME"] = "Fido"
    os.environ["TEST_BARKING"] = "1"

    result = parser.from_env("TEST")
    assert result == {"name": "Fido", "barking": True, "kind": "Dog"}
    assert "Dog" in parser.parsers

    # Clean up for next test
    del os.environ["TEST_BARKING"]

    # Now import Cat using full class name
    os.environ["TEST_KIND"] = "tests.sdk.utils.test_discriminated_union.Cat"
    os.environ["TEST_NAME"] = "Whiskers"

    result = parser.from_env("TEST")
    assert result == {"name": "Whiskers", "kind": "Cat"}
    assert "Cat" in parser.parsers
    # Both parsers should be registered now
    assert len(parser.parsers) == 2


def test_discriminated_union_full_class_name_invalid_module(clean_env):
    """Test that invalid module names raise ImportError."""
    parser = DiscriminatedUnionEnvParser(parsers={})

    os.environ["TEST_KIND"] = "nonexistent.module.SomeClass"
    os.environ["TEST_NAME"] = "Test"

    with pytest.raises(ModuleNotFoundError):
        parser.from_env("TEST")


def test_discriminated_union_full_class_name_invalid_class(clean_env):
    """Test that invalid class names raise AttributeError."""
    parser = DiscriminatedUnionEnvParser(parsers={})

    os.environ["TEST_KIND"] = (
        "tests.sdk.utils.test_discriminated_union.NonexistentClass"
    )
    os.environ["TEST_NAME"] = "Test"

    with pytest.raises(AttributeError):
        parser.from_env("TEST")


def test_discriminated_union_kind_only_no_other_variables(clean_env):
    """Test that DiscriminatedUnionEnvParser handles types that define only a kind
    without any other variables."""
    # Create a parser with no additional fields (empty parser that returns MISSING)
    empty_parser = ModelEnvParser(parsers={}, descriptions={})
    parser = DiscriminatedUnionEnvParser(parsers={"EmptyKind": empty_parser})

    # Set KIND but no other environment variables
    os.environ["TEST_KIND"] = "EmptyKind"

    # Should return just the kind, not MISSING
    result = parser.from_env("TEST")
    assert result == {"kind": "EmptyKind"}


def test_discriminated_union_kind_only_multiple_kinds(clean_env):
    """Test that when KIND is set to a type with no fields among multiple kinds,
    it still works correctly."""
    # Create parsers - one with fields, one without
    empty_parser = ModelEnvParser(parsers={}, descriptions={})
    dog_parser = ModelEnvParser(
        parsers={"name": StrEnvParser(), "barking": BoolEnvParser()},
        descriptions={},
    )
    parser = DiscriminatedUnionEnvParser(
        parsers={"EmptyKind": empty_parser, "Dog": dog_parser}
    )

    # Set KIND to the empty type
    os.environ["TEST_KIND"] = "EmptyKind"

    # Should return just the kind
    result = parser.from_env("TEST")
    assert result == {"kind": "EmptyKind"}


def test_discriminated_union_no_kind_no_variables_returns_missing(clean_env):
    """Test that when KIND is not set and parser returns MISSING,
    the result is MISSING (not an empty dict with no kind)."""
    # Create a parser with no additional fields
    empty_parser = ModelEnvParser(parsers={}, descriptions={})
    non_empty_parser = ModelEnvParser(
        parsers={"name": StrEnvParser()},
        descriptions={},
    )
    parser = DiscriminatedUnionEnvParser(
        parsers={"EmptyKind": empty_parser, "NonEmpty": non_empty_parser}
    )

    # Don't set KIND or any other variables
    # Should return MISSING because there are multiple kinds and no KIND is set
    result = parser.from_env("TEST")
    assert result is MISSING


def test_discriminated_union_single_empty_kind_no_variables(clean_env):
    """Test that when there's exactly one empty kind and no env vars are set,
    the result is MISSING (the entry is not configured)."""
    # Create a single empty parser
    empty_parser = ModelEnvParser(parsers={}, descriptions={})
    parser = DiscriminatedUnionEnvParser(parsers={"EmptyKind": empty_parser})

    # Don't set any environment variables (not even KIND)
    # With a single kind, it should try the parser but still return MISSING
    # because there's no indication that this entry is configured
    result = parser.from_env("TEST")
    assert result is MISSING
