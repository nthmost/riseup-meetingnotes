"""
process_meeting_notes.py — CLI entry point for standalone use.

The pipeline logic now lives in two focused modules:
  process.py — deterministic orchestration (no AI)
  ai.py      — AI summary generation (only file that imports anthropic)

This file re-exports their public API for backward compatibility and
provides a CLI for running the pipeline directly.

Usage:
    python process_meeting_notes.py --date 2026_04_14 --input raw.txt
    python process_meeting_notes.py --date 2026_04_14 --no-summary
"""
import argparse
import re
import sys
from datetime import date as _date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

from process import process, fetch_meeting_number, fix_meeting_number
from ai import generate_summary


def main():
    parser = argparse.ArgumentParser(
        description='Process raw Noisebridge Riseup Pad notes into clean wiki format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--date', required=True,
                        help='Meeting date in YYYY_MM_DD format (e.g. 2026_02_17)')
    parser.add_argument('--input', '-i',
                        help='Input file path. Omit to read from stdin.')
    parser.add_argument('--output', '-o',
                        help='Output .wiki file path.')
    parser.add_argument('--no-summary', action='store_true',
                        help='Skip AI summary generation')
    parser.add_argument('--dry-run', action='store_true',
                        help='Process and print preview; do not write output')

    args = parser.parse_args()

    if not re.match(r'^\d{4}_\d{2}_\d{2}$', args.date):
        print(f"Error: --date must be in YYYY_MM_DD format (got: {args.date})")
        sys.exit(1)

    y, m, d = (int(x) for x in args.date.split('_'))
    try:
        meeting_date = _date(y, m, d)
    except ValueError as e:
        print(f"Error: invalid date {args.date}: {e}")
        sys.exit(1)
    if meeting_date.weekday() != 1:
        day_name = meeting_date.strftime('%A')
        print(f"Warning: {args.date} is a {day_name}, not a Tuesday.")
        response = input("  Proceed anyway? [y/N] ").strip().lower()
        if response != 'y':
            sys.exit(1)

    if args.input:
        raw_text = Path(args.input).read_text('utf-8')
        print(f"Input: {args.input}")
    else:
        print("Reading from stdin (Ctrl+D when done)...")
        raw_text = sys.stdin.read()

    page_title = f"Meeting_Notes_{args.date}"
    output_path = args.output or f"wiki_pages/{page_title}.wiki"

    print(f"Processing notes for {args.date}...")
    cleaned, _ = process(raw_text, date_str=args.date, generate_ai_summary=not args.no_summary)

    if args.dry_run:
        print(f"(--dry-run: skipping write to {output_path})")
        for line in cleaned.splitlines()[:40]:
            print(line)
    else:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(cleaned, 'utf-8')
        print(f"Saved: {output_path}")

    print("Done.")


if __name__ == '__main__':
    main()
