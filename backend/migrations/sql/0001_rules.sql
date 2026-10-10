-- Database-enforced rules that the ORM models cannot express. Applied by migration 0001.

-- 1. Append-only tables: the audit trail and the ledger can only grow.
--    Corrections are new rows (a reversal entry, a superseding event), never edits.
CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed (add a reversal or a new event instead)',
        TG_TABLE_NAME, TG_OP;
END $$;

CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER journal_entries_append_only BEFORE UPDATE OR DELETE ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER journal_entries_no_truncate BEFORE TRUNCATE ON journal_entries
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER journal_lines_append_only BEFORE UPDATE OR DELETE ON journal_lines
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER journal_lines_no_truncate BEFORE TRUNCATE ON journal_lines
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- 2. Every journal entry has at least two lines and balances in each currency.
--    Checked at COMMIT (deferred), so an entry and its lines can be inserted in any order.
CREATE FUNCTION journal_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    eid bigint;
    n integer;
    bad record;
BEGIN
    IF TG_TABLE_NAME = 'journal_entries' THEN eid := NEW.id; ELSE eid := NEW.entry_id; END IF;
    SELECT count(*) INTO n FROM journal_lines WHERE entry_id = eid;
    IF n < 2 THEN
        RAISE EXCEPTION 'journal entry % has % line(s); at least 2 are required', eid, n;
    END IF;
    SELECT currency, sum(debit) AS d, sum(credit) AS c INTO bad
      FROM journal_lines WHERE entry_id = eid
     GROUP BY currency HAVING sum(debit) <> sum(credit) LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'journal entry % does not balance in %: debits % vs credits %', eid, bad.currency, bad.d, bad.c;
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER journal_entries_balanced AFTER INSERT ON journal_entries
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_balanced();
CREATE CONSTRAINT TRIGGER journal_lines_balanced AFTER INSERT ON journal_lines
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_balanced();

-- 3. Live updates: every audit event is announced on the 'audit_events' channel (the UI listens via SSE).
CREATE FUNCTION audit_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('audit_events', json_build_object(
        'workspace_id', NEW.workspace_id, 'seq', NEW.seq, 'action', NEW.action,
        'subject_type', NEW.subject_type, 'subject_id', NEW.subject_id)::text);
    RETURN NEW;
END $$;

CREATE TRIGGER audit_events_notify AFTER INSERT ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_notify();

-- 4. Chart of accounts (small built-in set) and the single LLM cooldown row.
INSERT INTO accounts (code, name, type) VALUES
    ('1000', 'Cash at bank', 'asset'),
    ('1300', 'VAT / sales tax input', 'asset'),
    ('2000', 'Accounts payable', 'liability'),
    ('2100', 'Withholding tax payable', 'liability'),
    ('4900', 'Early-payment discounts received', 'income'),
    ('5000', 'General expenses', 'expense'),
    ('5100', 'Freight and logistics', 'expense'),
    ('5200', 'Materials and supplies', 'expense'),
    ('5300', 'Professional and other services', 'expense');

INSERT INTO llm_state (id, consecutive_429) VALUES (1, 0);
