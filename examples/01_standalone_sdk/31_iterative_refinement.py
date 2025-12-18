#!/usr/bin/env python3
"""
Iterative Refinement Example: COBOL to Java Refactoring

This example demonstrates an iterative refinement workflow where:
1. A refactoring agent converts COBOL files to Java files
2. A critique agent evaluates the quality of each conversion and provides scores
3. If the average score is below 90%, the process repeats with feedback

The workflow continues until the refactoring meets the quality threshold.

Source COBOL files can be obtained from:
https://github.com/aws-samples/aws-mainframe-modernization-carddemo/tree/main/app/cbl
"""

import os
import re
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.tools.preset.default import get_default_agent


QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "90.0"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "5"))


def setup_workspace() -> tuple[Path, Path, Path]:
    """Create workspace directories for the refactoring workflow."""
    workspace_dir = Path(tempfile.mkdtemp())
    cobol_dir = workspace_dir / "cobol"
    java_dir = workspace_dir / "java"
    critique_dir = workspace_dir / "critiques"

    cobol_dir.mkdir(parents=True, exist_ok=True)
    java_dir.mkdir(parents=True, exist_ok=True)
    critique_dir.mkdir(parents=True, exist_ok=True)

    return workspace_dir, cobol_dir, java_dir


def create_sample_cobol_files(cobol_dir: Path) -> list[str]:
    """Create sample COBOL files for demonstration.

    In a real scenario, you would clone files from:
    https://github.com/aws-samples/aws-mainframe-modernization-carddemo/tree/main/app/cbl
    """
    sample_files = {
        "CBACT01C.cbl": """       IDENTIFICATION DIVISION.
       PROGRAM-ID. CBACT01C.
      *****************************************************************
      * Program: CBACT01C - Account Display Program
      * Purpose: Display account information for a given account number
      *****************************************************************
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-ACCOUNT-ID          PIC 9(11).
       01  WS-ACCOUNT-STATUS      PIC X(1).
       01  WS-ACCOUNT-BALANCE     PIC S9(13)V99.
       01  WS-CUSTOMER-NAME       PIC X(50).
       01  WS-ERROR-MSG           PIC X(80).

       PROCEDURE DIVISION.
           PERFORM 1000-INIT.
           PERFORM 2000-PROCESS.
           PERFORM 3000-TERMINATE.
           STOP RUN.

       1000-INIT.
           INITIALIZE WS-ACCOUNT-ID
           INITIALIZE WS-ACCOUNT-STATUS
           INITIALIZE WS-ACCOUNT-BALANCE
           INITIALIZE WS-CUSTOMER-NAME.

       2000-PROCESS.
           DISPLAY "ENTER ACCOUNT NUMBER: "
           ACCEPT WS-ACCOUNT-ID
           IF WS-ACCOUNT-ID = ZEROS
               MOVE "INVALID ACCOUNT NUMBER" TO WS-ERROR-MSG
               DISPLAY WS-ERROR-MSG
           ELSE
               DISPLAY "ACCOUNT: " WS-ACCOUNT-ID
               DISPLAY "STATUS: " WS-ACCOUNT-STATUS
               DISPLAY "BALANCE: " WS-ACCOUNT-BALANCE
           END-IF.

       3000-TERMINATE.
           DISPLAY "PROGRAM COMPLETE".
""",
        "CBCUS01C.cbl": """       IDENTIFICATION DIVISION.
       PROGRAM-ID. CBCUS01C.
      *****************************************************************
      * Program: CBCUS01C - Customer Information Program
      * Purpose: Manage customer data operations
      *****************************************************************
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-CUSTOMER-ID         PIC 9(9).
       01  WS-FIRST-NAME          PIC X(25).
       01  WS-LAST-NAME           PIC X(25).
       01  WS-ADDRESS             PIC X(100).
       01  WS-PHONE               PIC X(15).
       01  WS-EMAIL               PIC X(50).
       01  WS-OPERATION           PIC X(1).
           88 OP-ADD              VALUE 'A'.
           88 OP-UPDATE           VALUE 'U'.
           88 OP-DELETE           VALUE 'D'.
           88 OP-DISPLAY          VALUE 'V'.

       PROCEDURE DIVISION.
           PERFORM 1000-MAIN-PROCESS.
           STOP RUN.

       1000-MAIN-PROCESS.
           DISPLAY "CUSTOMER MANAGEMENT SYSTEM"
           DISPLAY "A-ADD U-UPDATE D-DELETE V-VIEW"
           ACCEPT WS-OPERATION
           EVALUATE TRUE
               WHEN OP-ADD
                   PERFORM 2000-ADD-CUSTOMER
               WHEN OP-UPDATE
                   PERFORM 3000-UPDATE-CUSTOMER
               WHEN OP-DELETE
                   PERFORM 4000-DELETE-CUSTOMER
               WHEN OP-DISPLAY
                   PERFORM 5000-DISPLAY-CUSTOMER
               WHEN OTHER
                   DISPLAY "INVALID OPERATION"
           END-EVALUATE.

       2000-ADD-CUSTOMER.
           DISPLAY "ADDING NEW CUSTOMER"
           ACCEPT WS-CUSTOMER-ID
           ACCEPT WS-FIRST-NAME
           ACCEPT WS-LAST-NAME
           DISPLAY "CUSTOMER ADDED: " WS-CUSTOMER-ID.

       3000-UPDATE-CUSTOMER.
           DISPLAY "UPDATING CUSTOMER"
           ACCEPT WS-CUSTOMER-ID
           DISPLAY "CUSTOMER UPDATED: " WS-CUSTOMER-ID.

       4000-DELETE-CUSTOMER.
           DISPLAY "DELETING CUSTOMER"
           ACCEPT WS-CUSTOMER-ID
           DISPLAY "CUSTOMER DELETED: " WS-CUSTOMER-ID.

       5000-DISPLAY-CUSTOMER.
           DISPLAY "DISPLAYING CUSTOMER"
           ACCEPT WS-CUSTOMER-ID
           DISPLAY "ID: " WS-CUSTOMER-ID
           DISPLAY "NAME: " WS-FIRST-NAME " " WS-LAST-NAME.
""",
        "CBTRN01C.cbl": """       IDENTIFICATION DIVISION.
       PROGRAM-ID. CBTRN01C.
      *****************************************************************
      * Program: CBTRN01C - Transaction Processing Program
      * Purpose: Process financial transactions
      *****************************************************************
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-TRANS-ID            PIC 9(16).
       01  WS-TRANS-TYPE          PIC X(2).
           88 TRANS-CREDIT        VALUE 'CR'.
           88 TRANS-DEBIT         VALUE 'DB'.
           88 TRANS-TRANSFER      VALUE 'TR'.
       01  WS-TRANS-AMOUNT        PIC S9(13)V99.
       01  WS-FROM-ACCOUNT        PIC 9(11).
       01  WS-TO-ACCOUNT          PIC 9(11).
       01  WS-TRANS-DATE          PIC 9(8).
       01  WS-TRANS-STATUS        PIC X(10).

       PROCEDURE DIVISION.
           PERFORM 1000-INITIALIZE.
           PERFORM 2000-PROCESS-TRANSACTION.
           PERFORM 3000-FINALIZE.
           STOP RUN.

       1000-INITIALIZE.
           MOVE ZEROS TO WS-TRANS-ID
           MOVE SPACES TO WS-TRANS-TYPE
           MOVE ZEROS TO WS-TRANS-AMOUNT
           MOVE "PENDING" TO WS-TRANS-STATUS.

       2000-PROCESS-TRANSACTION.
           DISPLAY "ENTER TRANSACTION TYPE (CR/DB/TR): "
           ACCEPT WS-TRANS-TYPE
           DISPLAY "ENTER AMOUNT: "
           ACCEPT WS-TRANS-AMOUNT
           EVALUATE TRUE
               WHEN TRANS-CREDIT
                   PERFORM 2100-PROCESS-CREDIT
               WHEN TRANS-DEBIT
                   PERFORM 2200-PROCESS-DEBIT
               WHEN TRANS-TRANSFER
                   PERFORM 2300-PROCESS-TRANSFER
               WHEN OTHER
                   MOVE "INVALID" TO WS-TRANS-STATUS
           END-EVALUATE.

       2100-PROCESS-CREDIT.
           DISPLAY "PROCESSING CREDIT"
           ACCEPT WS-TO-ACCOUNT
           MOVE "COMPLETED" TO WS-TRANS-STATUS
           DISPLAY "CREDIT APPLIED TO: " WS-TO-ACCOUNT.

       2200-PROCESS-DEBIT.
           DISPLAY "PROCESSING DEBIT"
           ACCEPT WS-FROM-ACCOUNT
           MOVE "COMPLETED" TO WS-TRANS-STATUS
           DISPLAY "DEBIT FROM: " WS-FROM-ACCOUNT.

       2300-PROCESS-TRANSFER.
           DISPLAY "PROCESSING TRANSFER"
           ACCEPT WS-FROM-ACCOUNT
           ACCEPT WS-TO-ACCOUNT
           MOVE "COMPLETED" TO WS-TRANS-STATUS
           DISPLAY "TRANSFER FROM " WS-FROM-ACCOUNT " TO " WS-TO-ACCOUNT.

       3000-FINALIZE.
           DISPLAY "TRANSACTION STATUS: " WS-TRANS-STATUS.
""",
    }

    created_files = []
    for filename, content in sample_files.items():
        file_path = cobol_dir / filename
        file_path.write_text(content)
        created_files.append(filename)

    return created_files


def get_refactoring_prompt(
    cobol_dir: Path,
    java_dir: Path,
    cobol_files: list[str],
    critique_file: Path | None = None,
) -> str:
    """Generate the prompt for the refactoring agent."""
    files_list = "\n".join(f"  - {f}" for f in cobol_files)

    base_prompt = f"""Convert the following COBOL files to Java:

COBOL Source Directory: {cobol_dir}
Java Target Directory: {java_dir}

Files to convert:
{files_list}

Requirements:
1. Create a Java class for each COBOL program
2. Preserve the business logic and data structures
3. Use appropriate Java naming conventions (camelCase for methods, PascalCase)
4. Convert COBOL data types to appropriate Java types
5. Implement proper error handling with try-catch blocks
6. Add JavaDoc comments explaining the purpose of each class and method
7. In JavaDoc comments, include traceability to the original COBOL source using
   the format: @source <program>:<line numbers> (e.g., @source CBACT01C.cbl:73-77)
8. Create a clean, maintainable object-oriented design
9. Each Java file should be compilable and follow Java best practices

Read each COBOL file and create the corresponding Java file in the target directory.
"""

    if critique_file and critique_file.exists():
        base_prompt += f"""

IMPORTANT: A previous refactoring attempt was evaluated and needs improvement.
Please review the critique at: {critique_file}
Address all issues mentioned in the critique to improve the conversion quality.
"""

    return base_prompt


def get_critique_prompt(
    cobol_dir: Path,
    java_dir: Path,
    cobol_files: list[str],
) -> str:
    """Generate the prompt for the critique agent."""
    files_list = "\n".join(f"  - {f}" for f in cobol_files)

    return f"""Evaluate the quality of COBOL to Java refactoring.

COBOL Source Directory: {cobol_dir}
Java Target Directory: {java_dir}

Original COBOL files:
{files_list}

Please evaluate each converted Java file against its original COBOL source.

For each file, assess:
1. Correctness: Does the Java code preserve the original business logic? (0-25 pts)
2. Code Quality: Is the code clean, readable, following Java conventions? (0-25 pts)
3. Completeness: Are all COBOL features properly converted? (0-25 pts)
4. Best Practices: Does it use proper OOP, error handling, documentation? (0-25 pts)

Create a critique report in the following EXACT format:

# COBOL to Java Refactoring Critique Report

## Summary
[Brief overall assessment]

## File Evaluations

### [Original COBOL filename]
- **Java File**: [corresponding Java filename or "NOT FOUND"]
- **Correctness**: [score]/25 - [brief explanation]
- **Code Quality**: [score]/25 - [brief explanation]
- **Completeness**: [score]/25 - [brief explanation]
- **Best Practices**: [score]/25 - [brief explanation]
- **File Score**: [total]/100
- **Issues to Address**:
  - [specific issue 1]
  - [specific issue 2]
  ...

[Repeat for each file]

## Overall Score
- **Average Score**: [calculated average of all file scores]
- **Recommendation**: [PASS if average >= 90, NEEDS_IMPROVEMENT otherwise]

## Priority Improvements
1. [Most critical improvement needed]
2. [Second priority]
3. [Third priority]

Save this report to: {java_dir.parent}/critiques/critique_report.md
"""


def parse_critique_score(critique_file: Path) -> float:
    """Parse the average score from the critique report."""
    if not critique_file.exists():
        return 0.0

    content = critique_file.read_text()

    # Look for "Average Score: X" pattern
    patterns = [
        r"\*\*Average Score\*\*:\s*(\d+(?:\.\d+)?)",
        r"Average Score:\s*(\d+(?:\.\d+)?)",
        r"average.*?(\d+(?:\.\d+)?)\s*(?:/100|%|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return float(match.group(1))

    return 0.0


def run_iterative_refinement() -> None:
    """Run the iterative refinement workflow."""
    # Setup
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
        usage_id="iterative_refinement",
    )

    workspace_dir, cobol_dir, java_dir = setup_workspace()
    critique_dir = workspace_dir / "critiques"

    print(f"Workspace: {workspace_dir}")
    print(f"COBOL Directory: {cobol_dir}")
    print(f"Java Directory: {java_dir}")
    print(f"Critique Directory: {critique_dir}")
    print()

    # Create sample COBOL files
    cobol_files = create_sample_cobol_files(cobol_dir)
    print(f"Created {len(cobol_files)} sample COBOL files:")
    for f in cobol_files:
        print(f"  - {f}")
    print()

    critique_file = critique_dir / "critique_report.md"
    current_score = 0.0
    iteration = 0

    while current_score < QUALITY_THRESHOLD and iteration < MAX_ITERATIONS:
        iteration += 1
        print("=" * 80)
        print(f"ITERATION {iteration}")
        print("=" * 80)

        # Phase 1: Refactoring
        print("\n--- Phase 1: Refactoring Agent ---")
        refactoring_agent = get_default_agent(llm=llm, cli_mode=True)
        refactoring_conversation = Conversation(
            agent=refactoring_agent,
            workspace=str(workspace_dir),
        )

        previous_critique = critique_file if iteration > 1 else None
        refactoring_prompt = get_refactoring_prompt(
            cobol_dir, java_dir, cobol_files, previous_critique
        )

        refactoring_conversation.send_message(refactoring_prompt)
        refactoring_conversation.run()
        print("Refactoring phase complete.")

        # Phase 2: Critique
        print("\n--- Phase 2: Critique Agent ---")
        critique_agent = get_default_agent(llm=llm, cli_mode=True)
        critique_conversation = Conversation(
            agent=critique_agent,
            workspace=str(workspace_dir),
        )

        critique_prompt = get_critique_prompt(cobol_dir, java_dir, cobol_files)
        critique_conversation.send_message(critique_prompt)
        critique_conversation.run()
        print("Critique phase complete.")

        # Parse the score
        current_score = parse_critique_score(critique_file)
        print(f"\nCurrent Score: {current_score:.1f}%")

        if current_score >= QUALITY_THRESHOLD:
            print(f"\n✓ Quality threshold ({QUALITY_THRESHOLD}%) met!")
        else:
            print(
                f"\n✗ Score below threshold ({QUALITY_THRESHOLD}%). "
                "Continuing refinement..."
            )

    # Final summary
    print("\n" + "=" * 80)
    print("ITERATIVE REFINEMENT COMPLETE")
    print("=" * 80)
    print(f"Total iterations: {iteration}")
    print(f"Final score: {current_score:.1f}%")
    print(f"Workspace: {workspace_dir}")

    # List created Java files
    print("\nCreated Java files:")
    for java_file in java_dir.glob("*.java"):
        print(f"  - {java_file.name}")

    # Show critique file location
    if critique_file.exists():
        print(f"\nFinal critique report: {critique_file}")

    # Report cost
    cost = llm.metrics.accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")


if __name__ == "__main__":
    run_iterative_refinement()
