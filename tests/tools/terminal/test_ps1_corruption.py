"""
Tests demonstrating PS1 metadata corruption issues.

This test file documents bugs where PS1 JSON blocks get corrupted by:
1. ASCII art from programs like grunt (cat mascot)
2. Pager programs like `less` that produce full-screen output

These tests are expected to FAIL until the underlying issues are fixed.

See Datadog logs for eval job eval-21310432128-claude-son for real-world examples.

BUG EXPLANATION:
----------------
The PS1 regex uses non-greedy matching: `###PS1JSON###(.*?)###PS1END###`

When grunt's ASCII cat art is interleaved with the PS1 prompt, the FIRST
###PS1JSON### block never gets its matching ###PS1END### (because the output
is corrupted). The regex then matches from the FIRST ###PS1JSON### all the
way to the ONLY ###PS1END### at the end.

This creates ONE giant match containing:
- Corrupted first JSON block
- ASCII cat art
- Command output
- Second (valid) JSON block

The combined content fails JSON parsing → 0 valid matches → AssertionError.
"""

from unittest.mock import MagicMock

import pytest

from openhands.tools.terminal.constants import CMD_OUTPUT_METADATA_PS1_REGEX
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal.terminal_session import TerminalSession


class TestPS1Corruption:
    """Tests for PS1 metadata block corruption by command output."""

    # Actual corrupted output from Datadog logs (eval-21310432128-claude-son)
    # The grunt ASCII cat art interrupts the PS1 JSON block
    #
    # STRUCTURE OF THIS OUTPUT:
    # 1. ###PS1JSON### (first block starts)
    # 2. JSON fields start but NO closing }
    # 3. ASCII cat art from grunt gets interleaved
    # 4. Test output ("8 passing", "Done.")
    # 5. ###PS1JSON### (second block starts)
    # 6. Complete valid JSON with }
    # 7. ###PS1END### (the ONLY end marker)
    #
    # The non-greedy regex matches from #1 to #7, creating ONE invalid match.
    CORRUPTED_OUTPUT_GRUNT_CAT = r"""
###PS1JSON###
{
  "pid": "",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "runtime-uerbtodceoavkhsd-5f46cc485d-297jp",
  "working_dir": "/workspace/p5.js",
  "py_interpreter_path": "/usr/bin/python"
 8   -_-_-_-_-_,------,
 0#PS-_-_-_-_-_|   /\_/\
 0 /w-_-_-_-_-^|__( ^ .^) eout 300 npm test 2>&1 | tail -50
     -_-_-_-_-  ""  ""

  8 passing (6ms)


Done.

###PS1JSON###
{
  "pid": "",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "runtime-uerbtodceoavkhsd-5f46cc485d-297jp",
  "working_dir": "/workspace/p5.js",
  "py_interpreter_path": "/usr/bin/python"
}
###PS1END###"""

    # Another corrupted output with ANSI remnants
    CORRUPTED_OUTPUT_ANSI_REMNANTS = r"""
###PS1JSON###
{
  "pid": "877",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "runtime-wurijejgnynchahc-f9f4f7f-ndqfp",
  "working_dir": "/workspace/p5.js",
  "py_interpreter_path": "/usr/bin/python"
 8   -_-_-_-_-_,------,
 0#PS-_-_-_-_-_|   /\_/\
 0 /w-_-_-_-_-^|__( ^ .^)  run grunt -- mochaTest:test 2>&1 | tail -30
     -_-_-_-_-  ""  ""

  8 passing (16ms)


Done.

###PS1JSON###
{
  "pid": "877",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "runtime-wurijejgnynchahc-f9f4f7f-ndqfp",
  "working_dir": "/workspace/p5.js",
  "py_interpreter_path": "/usr/bin/python"
}
###PS1END###"""

    # Pager output (like from `less` or `help` command) that has no PS1 markers
    # This happens when a pager takes over the terminal screen
    PAGER_OUTPUT_NO_PS1 = """Help on class RidgeClassifierCV in sklearn.linear_model:

class RidgeClassifierCV(sklearn.linear_model.base.LinearClassifierMixin, _BaseRidgeCV)
 |  Ridge classifier with built-in cross-validation.
 |
 |  By default, it performs Generalized Cross-Validation, which is a form of
 |  efficient Leave-One-Out cross-validation. Currently, only the n_features >
 |  n_samples case is handled efficiently.
 |
 |  Read more in the :ref:`User Guide <ridge_regression>`.
 |
 |  Parameters
 |  ----------
 |  alphas : numpy array of shape [n_alphas]
~
~
~
~
~
(END)"""

    def test_corrupted_ps1_regex_matches_wrong_content(self):
        """
        Test demonstrating the regex bug with corrupted PS1 output.

        The non-greedy regex `###PS1JSON###(.*?)###PS1END###` matches from
        the FIRST ###PS1JSON### to the ONLY ###PS1END###, creating ONE match
        that contains corrupted JSON (ASCII art interleaved).

        EXPECTED: The regex should somehow detect that the second ###PS1JSON###
        indicates the first block was never closed, and handle this gracefully.

        ACTUAL: The regex creates one giant match with invalid JSON content.

        This test PASSES because it documents the current (buggy) behavior.
        """
        # Get raw regex matches (before JSON validation)
        raw_matches = list(
            CMD_OUTPUT_METADATA_PS1_REGEX.finditer(self.CORRUPTED_OUTPUT_GRUNT_CAT)
        )

        # The regex finds exactly 1 match (from first PS1JSON to only PS1END)
        assert len(raw_matches) == 1, (
            f"Expected exactly 1 raw regex match, got {len(raw_matches)}. "
            "The non-greedy regex matches from first PS1JSON to the only PS1END."
        )

        # The matched content includes the SECOND ###PS1JSON### marker inside!
        # This is the bug - the regex doesn't understand block boundaries.
        matched_content = raw_matches[0].group(1)
        assert "###PS1JSON###" in matched_content, (
            "The matched content should contain another ###PS1JSON### marker, "
            "proving that the regex incorrectly spans multiple intended blocks."
        )

    def test_corrupted_ps1_causes_zero_valid_matches(self):
        """
        Test that grunt's ASCII cat art causes ZERO valid PS1 matches.

        This is the ROOT CAUSE of the production error:
        "Expected at least one PS1 metadata block, but got 0."

        The regex matches one block but the content fails JSON parsing
        because it contains ASCII art and a nested ###PS1JSON### marker.

        This test FAILS because the current behavior is broken - we get
        0 matches when there IS a valid JSON block at the end of the output.
        A fix should return at least 1 valid match.
        """
        matches = CmdOutputMetadata.matches_ps1_metadata(
            self.CORRUPTED_OUTPUT_GRUNT_CAT
        )

        # CURRENT BUGGY BEHAVIOR: 0 matches (all fail JSON parsing)
        # EXPECTED FIXED BEHAVIOR: 1 match (the second valid block)
        assert len(matches) >= 1, (
            f"BUG: Expected at least 1 valid PS1 match, got {len(matches)}. "
            "The output contains a VALID PS1 block at the end, but the "
            "regex/parser fails to find it. This bug causes "
            "'Expected at least one PS1 metadata block, but got 0' errors."
        )

    def test_handle_completed_command_fails_with_corrupted_output(self):
        """
        Test that _handle_completed_command raises AssertionError with corrupted output.

        When terminal output is corrupted such that NO valid PS1 blocks are found,
        the assertion in _handle_completed_command fails with:
        "Expected at least one PS1 metadata block, but got 0."

        This is the actual error seen in production (Datadog logs).
        """
        # Create a mock terminal interface
        mock_terminal = MagicMock()
        mock_terminal.work_dir = "/workspace"
        mock_terminal.username = None

        # Create session
        session = TerminalSession(terminal=mock_terminal)

        # Simulate output where ALL PS1 blocks are corrupted
        # In this case, the JSON is completely broken - no valid blocks at all
        completely_corrupted_output = """\n###PS1JSON###
{
  "pid": "",
  "exit_code": "0",
  "username": "openhands",
 8   -_-_-_-_-_,------,
 0#PS-_-_-_-_-_|   /\\_/\\
 ASCII ART BREAKS THE JSON
###PS1JSON###
ALSO BROKEN
{invalid json here}
###PS1END###"""

        ps1_matches = CmdOutputMetadata.matches_ps1_metadata(
            completely_corrupted_output
        )

        # Verify we get 0 matches due to corruption
        assert len(ps1_matches) == 0, (
            f"Expected 0 PS1 matches from corrupted output, got {len(ps1_matches)}"
        )

        # Now verify the assertion fails as seen in production
        with pytest.raises(AssertionError) as exc_info:
            session._handle_completed_command(
                command="npm test",
                terminal_content=completely_corrupted_output,
                ps1_matches=ps1_matches,
            )

        # Verify the error message matches what we see in Datadog
        error_msg = str(exc_info.value)
        assert "Expected at least one PS1 metadata block, but got 0" in error_msg
        assert "FULL OUTPUT" in error_msg

    def test_pager_output_causes_zero_ps1_matches(self):
        """
        Test that pager output (like `less`) produces zero PS1 matches.

        When a command opens a pager (like `help(some_func)` in Python REPL
        or `man ls`), the pager takes over the terminal screen. The PS1
        prompt never appears because the pager is interactive and waiting
        for user input.

        This causes "Expected exactly one PS1 metadata block BEFORE the
        execution of a command, but got 0 PS1 metadata blocks" warnings.
        """
        matches = CmdOutputMetadata.matches_ps1_metadata(self.PAGER_OUTPUT_NO_PS1)

        assert len(matches) == 0, (
            f"Expected 0 PS1 matches from pager output, got {len(matches)}"
        )

    def test_partial_ps1_block_not_matched(self):
        """
        Test that a partial PS1 block (missing ###PS1END###) is not matched.

        This simulates the scenario where the PS1 prompt starts printing
        but gets interrupted before completing. The regex should NOT match
        incomplete blocks.
        """
        # PS1 block that starts but never ends (common in corruption scenarios)
        partial_block = """
###PS1JSON###
{
  "pid": "123",
  "exit_code": "0",
  "username": "openhands"
}
SOME EXTRA OUTPUT BUT NO PS1END MARKER
"""
        matches = CmdOutputMetadata.matches_ps1_metadata(partial_block)
        assert len(matches) == 0, (
            f"Expected 0 matches for partial PS1 block, got {len(matches)}"
        )

    def test_ps1_block_with_embedded_special_chars(self):
        """
        Test PS1 parsing when special characters appear in the JSON block.

        The grunt cat ASCII art contains characters like:
        - `#PS` which looks like a partial marker
        - Backslashes `\\`
        - Underscores and dashes in patterns

        These should not confuse the parser when they appear inside the JSON.
        """
        # Valid PS1 block but with special chars in a field value
        ps1_with_special_chars = """
###PS1JSON###
{
  "pid": "123",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "host-with-#PS-in-name",
  "working_dir": "/path/with\\backslash",
  "py_interpreter_path": "/usr/bin/python"
}
###PS1END###
"""
        matches = CmdOutputMetadata.matches_ps1_metadata(ps1_with_special_chars)
        assert len(matches) == 1, (
            f"Expected 1 match for PS1 with special chars in values, got {len(matches)}"
        )

    def test_interleaved_output_between_ps1_markers(self):
        """
        Test that command output interleaved between PS1 markers corrupts parsing.

        This is the actual failure mode observed in production:
        1. PS1 prompt starts printing (###PS1JSON###)
        2. Command output gets printed (ASCII art)
        3. PS1 prompt JSON is incomplete/corrupted
        4. Another PS1 block might appear later

        The parser should handle this gracefully instead of crashing.
        """
        interleaved_output = """
###PS1JSON###
{
  "pid": "123"
INTERLEAVED COMMAND OUTPUT HERE - THIS BREAKS THE JSON
}
###PS1END###
"""
        matches = CmdOutputMetadata.matches_ps1_metadata(interleaved_output)

        # The regex WILL match this because the markers are present,
        # but the JSON parsing should fail and skip it
        assert len(matches) == 0, (
            f"Expected 0 matches with interleaved output, got {len(matches)}. "
            "The JSON parser should reject malformed JSON between markers."
        )


class TestPS1CorruptionIntegration:
    """Integration tests for PS1 corruption scenarios."""

    def test_terminal_session_handles_corrupted_output_gracefully(self):
        """
        Test that TerminalSession handles corrupted PS1 output gracefully.

        Currently, this raises an AssertionError which causes the evaluation
        to fail. This test documents the current (problematic) behavior.

        EXPECTED BEHAVIOR (after fix):
        - Session should retry reading terminal output
        - OR return a sensible default observation
        - OR log a warning but not crash

        CURRENT BEHAVIOR:
        - Raises AssertionError and crashes the evaluation
        """
        mock_terminal = MagicMock()
        mock_terminal.work_dir = "/workspace"
        mock_terminal.username = None

        session = TerminalSession(terminal=mock_terminal)

        # Empty PS1 matches list (as would happen with completely corrupted output)
        empty_matches = []

        # This SHOULD NOT raise an error in production, but currently it does
        with pytest.raises(AssertionError) as exc_info:
            session._handle_completed_command(
                command="echo test",
                terminal_content="completely garbled output with no PS1 markers",
                ps1_matches=empty_matches,
            )

        # Document the current error behavior
        error_msg = str(exc_info.value)
        assert "Expected at least one PS1 metadata block, but got 0" in error_msg


class TestPS1ParserRobustness:
    """Tests for PS1 parser robustness improvements."""

    def test_regex_handles_multiline_json(self):
        """Test that the PS1 regex correctly handles multiline JSON."""
        multiline_json = """
###PS1JSON###
{
  "pid": "123",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "localhost",
  "working_dir": "/home/user",
  "py_interpreter_path": "/usr/bin/python"
}
###PS1END###
"""
        matches = CmdOutputMetadata.matches_ps1_metadata(multiline_json)
        assert len(matches) == 1

    def test_multiple_valid_ps1_blocks(self):
        """Test parsing multiple valid PS1 blocks (normal operation)."""
        two_blocks = """
###PS1JSON###
{
  "pid": "100",
  "exit_code": "0",
  "username": "user1"
}
###PS1END###
Some command output here
###PS1JSON###
{
  "pid": "101",
  "exit_code": "1",
  "username": "user1"
}
###PS1END###
"""
        matches = CmdOutputMetadata.matches_ps1_metadata(two_blocks)
        assert len(matches) == 2

        # Verify we can extract data from both
        meta1 = CmdOutputMetadata.from_ps1_match(matches[0])
        meta2 = CmdOutputMetadata.from_ps1_match(matches[1])
        assert meta1.pid == 100
        assert meta2.pid == 101
        assert meta1.exit_code == 0
        assert meta2.exit_code == 1


class TestSyntheticMatchPositionBug:
    """Tests for the critical bug in _SyntheticMatch position methods.

    The _SyntheticMatch class returns positions from the original corrupted match,
    not the actual position of the recovered content. This causes incorrect slicing
    in terminal_session.py when extracting command output between PS1 blocks.

    See: https://github.com/OpenHands/software-agent-sdk/pull/1817#discussion_r2727556034
    """

    # Corrupted output where ASCII art interrupts the first PS1 block
    # The second PS1 block is valid and should be recovered
    CORRUPTED_OUTPUT = """\
COMMAND OUTPUT BEFORE PS1
###PS1JSON###
{
  "pid": "123",
  "exit_code": "0",
  "username": "openhands"
ASCII ART CORRUPTS THIS BLOCK
###PS1JSON###
{
  "pid": "456",
  "exit_code": "0",
  "username": "openhands",
  "hostname": "localhost",
  "working_dir": "/workspace",
  "py_interpreter_path": "/usr/bin/python"
}
###PS1END###
COMMAND OUTPUT AFTER PS1"""

    def test_synthetic_match_positions_are_incorrect(self):
        """
        CRITICAL BUG: _SyntheticMatch.start() and .end() return wrong positions.

        When a PS1 block is corrupted and we recover the nested valid block,
        the _SyntheticMatch class returns positions from the ORIGINAL corrupted
        match, not the actual position of the recovered content.

        This causes terminal_session.py to extract wrong content when slicing:
            terminal_content[match.start():match.end()]

        The sliced content will include the corrupted first block and ASCII art,
        instead of just the recovered valid PS1 block.
        """
        matches = CmdOutputMetadata.matches_ps1_metadata(self.CORRUPTED_OUTPUT)

        # We should get 1 match (the recovered valid block)
        assert len(matches) == 1, f"Expected 1 recovered match, got {len(matches)}"

        match = matches[0]

        # The match.group(0) returns the synthetic full match (correct content)
        full_match_content = match.group(0)
        assert "###PS1JSON###" in full_match_content
        assert "###PS1END###" in full_match_content

        # The JSON content should be valid
        json_content = match.group(1)
        assert '"pid": "456"' in json_content
        assert '"working_dir": "/workspace"' in json_content

        # BUG: Using start() and end() to slice the original string
        # gives DIFFERENT content than match.group(0)
        sliced_content = self.CORRUPTED_OUTPUT[match.start() : match.end()]

        # This assertion demonstrates the bug:
        # The sliced content should equal match.group(0), but it doesn't!
        # The sliced content includes the corrupted first block and ASCII art.
        assert sliced_content == full_match_content, (
            f"CRITICAL BUG: Slicing with match positions gives wrong content!\n"
            f"Expected (from group(0)):\n{full_match_content!r}\n\n"
            f"Got (from slicing):\n{sliced_content!r}\n\n"
            f"The _SyntheticMatch.start() and .end() methods return positions "
            f"from the original corrupted match, not the recovered content position."
        )

    def test_content_after_synthetic_match_is_wrong(self):
        """
        Test that content AFTER a _SyntheticMatch is extracted incorrectly.

        When terminal_session.py extracts content after the last PS1 block using:
            terminal_content[ps1_matches[-1].end() + 1:]

        The _SyntheticMatch.end() returns the end of the ORIGINAL corrupted match,
        not the end of the recovered valid content. This means we miss content
        that appears after the recovered block but before the original match end.
        """
        # Terminal content where the synthetic match's end() is wrong
        terminal_content = self.CORRUPTED_OUTPUT

        matches = CmdOutputMetadata.matches_ps1_metadata(terminal_content)
        assert len(matches) == 1, f"Expected 1 match, got {len(matches)}"

        match = matches[0]

        # Get content after the PS1 block using match.end()
        # This is what terminal_session.py does
        content_after = terminal_content[match.end() + 1 :]

        # The expected content after the PS1 block is "COMMAND OUTPUT AFTER PS1"
        expected_after = "COMMAND OUTPUT AFTER PS1"

        assert expected_after in content_after, (
            f"CRITICAL BUG: Content after PS1 block is wrong!\n"
            f"Expected to find: {expected_after!r}\n"
            f"Got: {content_after!r}\n\n"
            f"match.end() = {match.end()}\n"
            f"len(terminal_content) = {len(terminal_content)}\n"
            f"The _SyntheticMatch.end() returns the wrong position."
        )

    def test_content_before_synthetic_match_includes_corrupted_data(self):
        """
        Test that content BEFORE a _SyntheticMatch includes corrupted data.

        When terminal_session.py extracts content before a PS1 block using:
            terminal_content[:ps1_matches[0].start()]

        The _SyntheticMatch.start() returns the start of the ORIGINAL corrupted
        match, not the start of the recovered valid content. This means the
        "content before" doesn't include the corrupted first block and ASCII art.
        """
        terminal_content = self.CORRUPTED_OUTPUT

        matches = CmdOutputMetadata.matches_ps1_metadata(terminal_content)
        assert len(matches) == 1, f"Expected 1 match, got {len(matches)}"

        match = matches[0]

        # Get content before the PS1 block using match.start()
        content_before = terminal_content[: match.start()]

        # The expected content before should be "COMMAND OUTPUT BEFORE PS1\n"
        # But due to the bug, we get less content because start() points to
        # the beginning of the corrupted block, not the recovered block
        expected_before = "COMMAND OUTPUT BEFORE PS1"

        assert expected_before in content_before, (
            f"Content before PS1 block should include: {expected_before!r}\n"
            f"Got: {content_before!r}\n"
            f"match.start() = {match.start()}"
        )

        # The actual position of the recovered content is AFTER the corrupted
        # first block. Let's verify the bug by checking that the content
        # between start() and the actual recovered content position is NOT empty
        recovered_json = match.group(1)
        actual_recovered_start = terminal_content.find(recovered_json)

        # The bug: match.start() points to the corrupted block start,
        # not the recovered content start
        content_between_start_and_recovered = terminal_content[
            match.start() : actual_recovered_start
        ]

        # This content should be empty if positions were correct,
        # but it contains the corrupted first block
        assert len(content_between_start_and_recovered) > 0, (
            "If positions were correct, there would be no content between "
            "match.start() and the actual recovered content position"
        )
        assert "ASCII ART CORRUPTS THIS BLOCK" in content_between_start_and_recovered, (
            f"CRITICAL BUG: The content between match.start() and actual "
            f"recovered position contains corrupted data:\n"
            f"{content_between_start_and_recovered!r}\n\n"
            f"This proves _SyntheticMatch.start() returns the wrong position."
        )
