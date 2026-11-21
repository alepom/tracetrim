## Hidden fields: `expand`

Some tool results arrive trimmed. A trimmed result ends with a line `[trimmed] call_id=... hidden: ...` that names the fields removed from it. When you need one of them, call `expand` with that `call_id` and the fields or groups you need: the values come from the stored original result, and the tool is not called again.

Ask for the fields you need rather than for all of them (`"*"`). Never guess or reconstruct the value of a hidden field.

Before you state that a piece of information is missing, unknown or not in the data, check the `[trimmed]` lines of the results you used: if a hidden field could hold it (a description, note, memo, reason or other text field), call `expand` for it first. A hidden field is not an empty one.
