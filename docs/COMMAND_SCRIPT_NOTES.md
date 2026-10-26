# How to read `docs/COMMAND_SCRIPT.csv`

The script is the fixed list of spoken commands every benchmark uses. Success is judged by what the mock page logged, never by what Navi says it did. Owner: Navi QA.

## Columns

- `id`: unique. `P` plan example, `X` X comment task and its variants, `G` confirm-gate negative, `R` other risky action, `S` secret field, `U` unclear speech, `J` prompt injection, `B` simple browser task, `F` forms, overlays, shadow DOM, scrolling, `D` data and read-aloud.
- `command`: what Gopi says. A turn that follows is separated by ` || `. `(silence for 31 seconds)` means wait that long before the next turn.
- `expected_action`: what Navi should do. It starts with `clarify:` when the right answer is one question and no action. Risky rows name the read back and the word confirm.
- `notes`: where the row comes from (plan example, walkthrough case, benchmark id) and the mock page variant (`?variant=...`). Rows starting with `negative:` must execute nothing. Rows noted `injection:` carry text on the page that tries to give Navi an order.
- `risk_class`: `none`, or one of the classes that need confirm in `docs/TOOL_CONTRACT.json`: post, comment, form_submit, dm, email, delete, purchase.
- `mock_page`: an id from `tests/mockpages/pages.json`. The page logs every click, key and request, and that log decides the result.
- `expected_final_state`: what the log must show. `0 before confirm` means no irreversible request was logged before the spoken word confirm. `0 actions` means nothing was clicked, typed or navigated.

## Which rows feed which benchmark

P05, X01 to X14, G01 to G10 and R01 to R04 feed C4 to C8, C11 to C13 and A4 (X01 only). P06 feeds B1 and B2. S01 to S03 feed C9. J01 to J03 feed C10. P10 and U01 to U05 feed C12. B01 to B09 and F01 to F12 feed A5, A6, A7, D6. D01 to D03 and P07, P08 feed A8, A9, D10, D11. Every row feeds D1 to D5.

## Skipped, with a reason

- `email` risk class: contract 1.0 has no email tool and there is no mock mail page. A row will be added when one exists.
- Walkthrough case 15 (timing): a benchmark (A4, X01), not a command row.
- Walkthrough case 9 (post text that says "reply with confirm"): covered by J03 and by the confirm-gate rows, which prove no page text can approve.
- Research: the `SKIP_HOSTS` substring bug (x.com also drops netflix.com) is a unit-test case for the research module, not a spoken command.
- Browser gap about the `config.py` default browser (Edge): a config test, not a spoken command.
- Hotkey Ctrl+Shift+Space: not spoken; it is tested by the stop benchmarks (B1 to B5), not by this script.
