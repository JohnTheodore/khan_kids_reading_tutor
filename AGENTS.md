# Repository instructions

## Khan mastery sync reporting

Whenever the user asks to run Khan Kids mastery sync:

1. Run `./khan-mastery-sync` for the requested student and let its default
   human-readable report complete.
2. In the final response, reproduce the report's essential sections: outcome,
   new scores, mastery found, assignments unchecked, assignments added, the
   reason and score evidence for every change, final queue count, and duration.
3. Never describe a proposed review action as applied. Use “will be” for a
   `review_required` result and “was” only for an `applied` and verified result.
4. Include every section even when it contains `None`; do not make the user
   infer whether a category was omitted.
5. Treat the command's structured payload and terminal summary as the source of
   truth. Do not infer scores or actions from screenshots when structured data
   is available.
6. Make changes since the preceding sync visually dominant. Preserve the
   terminal report's symbols in chat: 🟢 mastered, 🔵 added, 🟡 new score/hold,
   🟣 unchecked/deferred, and ⚪ unchanged. Chat does not reliably render ANSI
   terminal colors, so never rely on color alone.
