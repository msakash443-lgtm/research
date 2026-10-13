"""Database enforcement for the verified-claim evidence invariant."""

from sqlalchemy import DDL, event

from app.models import Claim, ClaimEvidence


SQLITE_VERIFY = """
CREATE TRIGGER claims_require_evidence_verify
BEFORE UPDATE OF verified ON claims
WHEN NEW.verified = 1 AND NOT EXISTS (
    SELECT 1 FROM claim_evidence WHERE claim_id = NEW.id
)
BEGIN SELECT RAISE(ABORT, 'verified claim requires evidence'); END
"""
SQLITE_INSERT = """
CREATE TRIGGER claims_require_evidence_insert
BEFORE INSERT ON claims
WHEN NEW.verified = 1
BEGIN SELECT RAISE(ABORT, 'verified claim requires evidence'); END
"""
SQLITE_DELETE = """
CREATE TRIGGER claims_keep_evidence_delete
BEFORE DELETE ON claim_evidence
WHEN (SELECT verified FROM claims WHERE id = OLD.claim_id) = 1
 AND NOT EXISTS (
    SELECT 1 FROM claim_evidence WHERE claim_id = OLD.claim_id AND id != OLD.id
 )
BEGIN SELECT RAISE(ABORT, 'verified claim requires evidence'); END
"""
SQLITE_MOVE = """
CREATE TRIGGER claims_keep_evidence_move
BEFORE UPDATE OF claim_id ON claim_evidence
WHEN NEW.claim_id != OLD.claim_id
 AND (SELECT verified FROM claims WHERE id = OLD.claim_id) = 1
 AND NOT EXISTS (
    SELECT 1 FROM claim_evidence WHERE claim_id = OLD.claim_id AND id != OLD.id
 )
BEGIN SELECT RAISE(ABORT, 'verified claim requires evidence'); END
"""
POSTGRES_FUNCTION = """
CREATE FUNCTION claim_evidence_guard() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'claims' THEN
        IF NEW.verified AND (TG_OP = 'INSERT' OR NOT EXISTS (
            SELECT 1 FROM claim_evidence WHERE claim_id = NEW.id
        )) THEN
            RAISE EXCEPTION 'verified claim requires evidence';
        END IF;
    ELSIF (TG_OP = 'DELETE' OR NEW.claim_id <> OLD.claim_id)
      AND EXISTS (SELECT 1 FROM claims WHERE id = OLD.claim_id AND verified FOR UPDATE)
      AND NOT EXISTS (
          SELECT 1 FROM claim_evidence WHERE claim_id = OLD.claim_id AND id <> OLD.id
      ) THEN
        RAISE EXCEPTION 'verified claim requires evidence';
    END IF;
    RETURN COALESCE(NEW, OLD);
END; $$ LANGUAGE plpgsql
"""
POSTGRES_TRIGGERS = (
    "CREATE TRIGGER claims_require_evidence_insert BEFORE INSERT ON claims FOR EACH ROW EXECUTE FUNCTION claim_evidence_guard()",
    "CREATE TRIGGER claims_require_evidence_verify BEFORE UPDATE OF verified ON claims FOR EACH ROW EXECUTE FUNCTION claim_evidence_guard()",
    "CREATE TRIGGER claims_keep_evidence_delete BEFORE DELETE ON claim_evidence FOR EACH ROW EXECUTE FUNCTION claim_evidence_guard()",
    "CREATE TRIGGER claims_keep_evidence_move BEFORE UPDATE OF claim_id ON claim_evidence FOR EACH ROW EXECUTE FUNCTION claim_evidence_guard()",
)


for statement in (SQLITE_VERIFY, SQLITE_INSERT):
    event.listen(Claim.__table__, "after_create", DDL(statement).execute_if(dialect="sqlite"))
event.listen(ClaimEvidence.__table__, "after_create", DDL(SQLITE_DELETE).execute_if(dialect="sqlite"))
event.listen(ClaimEvidence.__table__, "after_create", DDL(SQLITE_MOVE).execute_if(dialect="sqlite"))
event.listen(ClaimEvidence.__table__, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
for statement in POSTGRES_TRIGGERS:
    event.listen(ClaimEvidence.__table__, "after_create", DDL(statement).execute_if(dialect="postgresql"))
