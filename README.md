# Mental Math Quant Prep CLI

Terminal-based practice for quant-style mental math screens, including an `80 in 8` simulation mode.

## Disclaimer

- Unofficial practice tool built from public descriptions of quant-style mental math screens.
- Not affiliated with, endorsed by, or provided by any trading firm.
- Not guaranteed to match the exact question mix, scoring rules, or interface of any real interview assessment.

## Features

- Multiple-choice timed practice
- Real-mode `80 questions in 8 minutes`
- Net scoring for real mode with `+1 / -1 / 0`
- Fractions, decimals, arithmetic, and reverse-equation drills
- Post-run review with fastest solving route
- Weak-spot practice based on recent history

## Run

```bash
python3 mental_math_cli.py
```

Or start the real simulation directly:

```bash
python3 mental_math_cli.py --preset real
```

## Real Mode

- `80` questions in `8` minutes
- Multiple-choice
- No per-question timer
- Net scoring with `+1` for correct, `-1` for wrong, `0` for unanswered

## Notes

- Practice history is stored locally in `mental_math_history.jsonl`
- The script uses only the Python standard library
