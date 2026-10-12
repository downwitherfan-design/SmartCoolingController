# V2.1 Sprint Decisions

Engineering decisions taken during the sprint, with the trade-off recorded.

## Credential handling

**Decision.** The provided `ghp_…` token is used only as a transient git
credential (remote URL / `Authorization` header for this repository). Its value
is never written into a tracked file or the durable memory ledger.

**Trade-off.** The user asked to "store it in memory". Storing a live secret in
version control would leak it to anyone with repository read access and to the
public history. The functional goal — write access to this repository — is fully
met without persisting the raw value. Only a reference ("the repository GitHub
token") is recorded.

## Doc vs. engineering budget

**Decision.** Keep the ledger to the minimum useful form and spend the session
on implementation and verification, as instructed.

## Simulation honesty

**Decision.** Results from `app/simulation` and `app/protocol/mock_esp32.py` are
labelled as simulator results. No simulated outcome is described as a physical
ESP32 test. Windows-only execution steps are labelled untested when no Windows
host is available.
