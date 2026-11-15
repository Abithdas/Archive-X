# ax-faces: learning from corrections

Pure logic, no I/O: given the faces a person has confirmed and removed, it builds a profile per person and answers who a face is NOT, which faces in a group look out of place, and which unnamed faces look like a named person.

**Owns:** profile building with trimming of outliers, the thresholds (`SUGGEST_MIN`, `STRONG`, `NEGATIVE_MARGIN`) and the rival check.

**Does not own:** reading faces from the database or the routes (`ax-server/src/learning.rs`), or the face model itself (still Python; a better model is planned for Phase 6 of docs/plan.md).
