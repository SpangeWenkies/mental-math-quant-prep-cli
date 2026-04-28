# Mental Math Quant Prep CLI

Terminal-based practice for quant-style mental math screens, including an `80 in 8` simulation mode.

## Features

- Multiple-choice timed practice
- Real-mode `80 questions in 8 minutes`
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

## Notes

- Practice history is stored locally in `mental_math_history.jsonl`
- The script uses only the Python standard library
