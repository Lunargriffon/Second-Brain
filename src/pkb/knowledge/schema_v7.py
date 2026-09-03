"""Reviewed SQLite v7 statements for the Fact/Entity knowledge foundation."""

from __future__ import annotations


KNOWLEDGE_SCHEMA_V7: tuple[str, ...] = (
    """CREATE TABLE document_text_versions (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
        source_content_hash TEXT NOT NULL,
        normalized_content_hash TEXT NOT NULL,
        normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
        plain_content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        invalidated_at TEXT,
        invalidation_reason TEXT,
        UNIQUE(document_id, normalized_content_hash, normalization_version),
        CHECK((invalidated_at IS NULL) = (invalidation_reason IS NULL))
    )""",
    """CREATE TABLE entities (
        id INTEGER PRIMARY KEY,
        entity_type TEXT NOT NULL CHECK(entity_type IN (
            'person','organization','project','concept','decision','event','claim'
        )),
        canonical_name TEXT NOT NULL CHECK(length(trim(canonical_name)) > 0),
        normalized_name TEXT NOT NULL CHECK(length(normalized_name) > 0),
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','accepted','rejected')),
        merged_into_entity_id INTEGER REFERENCES entities(id) ON DELETE RESTRICT,
        creation_derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        reviewed_at TEXT,
        reviewed_by TEXT,
        review_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(merged_into_entity_id IS NULL OR merged_into_entity_id <> id),
        CHECK(review_status = 'accepted' OR merged_into_entity_id IS NULL)
    )""",
    """CREATE TABLE entity_aliases (
        id INTEGER PRIMARY KEY,
        entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
        alias_kind TEXT NOT NULL CHECK(alias_kind IN (
            'name','email','url','platform_id','organization_code','other'
        )),
        alias_value TEXT NOT NULL CHECK(length(trim(alias_value)) > 0),
        normalized_value TEXT NOT NULL CHECK(length(normalized_value) > 0),
        namespace TEXT NOT NULL DEFAULT '',
        is_strong_identifier INTEGER NOT NULL DEFAULT 0
            CHECK(is_strong_identifier IN (0,1)),
        source_document_id INTEGER REFERENCES documents(id) ON DELETE RESTRICT,
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','accepted','rejected')),
        confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        invalidated_at TEXT,
        invalidation_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK((invalidated_at IS NULL) = (invalidation_reason IS NULL))
    )""",
    """CREATE TABLE entity_mentions (
        id INTEGER PRIMARY KEY,
        document_text_version_id INTEGER NOT NULL
            REFERENCES document_text_versions(id) ON DELETE RESTRICT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
        normalized_content_hash TEXT NOT NULL,
        normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
        entity_id INTEGER REFERENCES entities(id) ON DELETE RESTRICT,
        surface_text TEXT NOT NULL CHECK(length(surface_text) > 0),
        start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
        end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
        mention_type TEXT NOT NULL CHECK(mention_type IN (
            'person','organization','project','concept','decision','event','claim','unknown'
        )),
        linking_method TEXT NOT NULL CHECK(linking_method IN (
            'unresolved','strong_identifier','exact_alias','heuristic','llm','human'
        )),
        linking_confidence REAL
            CHECK(linking_confidence IS NULL OR linking_confidence BETWEEN 0.0 AND 1.0),
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','accepted','rejected')),
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        invalidated_at TEXT,
        invalidation_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(document_id, normalized_content_hash, normalization_version)
            REFERENCES document_text_versions(
                document_id, normalized_content_hash, normalization_version
            ) ON DELETE RESTRICT,
        CHECK(review_status <> 'accepted' OR entity_id IS NOT NULL),
        CHECK(linking_method <> 'unresolved' OR entity_id IS NULL),
        CHECK((invalidated_at IS NULL) = (invalidation_reason IS NULL))
    )""",
    """CREATE TABLE facts (
        id INTEGER PRIMARY KEY,
        fact_key TEXT NOT NULL CHECK(length(fact_key) = 64),
        subject_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
        predicate TEXT NOT NULL CHECK(length(trim(predicate)) > 0),
        object_entity_id INTEGER REFERENCES entities(id) ON DELETE RESTRICT,
        object_value_json TEXT,
        object_type TEXT NOT NULL CHECK(object_type IN (
            'entity','string','number','boolean','date','datetime','money','quantity'
        )),
        object_normalized_text TEXT NOT NULL CHECK(length(object_normalized_text) > 0),
        valid_from TEXT,
        valid_to TEXT,
        observed_at TEXT,
        confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','accepted','rejected')),
        knowledge_status TEXT
            CHECK(knowledge_status IN ('active','disputed','superseded','retracted')),
        creation_derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        reviewed_at TEXT,
        reviewed_by TEXT,
        review_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK((object_entity_id IS NOT NULL) <> (object_value_json IS NOT NULL)),
        CHECK((object_type = 'entity') = (object_entity_id IS NOT NULL)),
        CHECK(object_value_json IS NULL OR json_valid(object_value_json)),
        CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to),
        CHECK(
            (review_status IN ('pending','rejected') AND knowledge_status IS NULL)
            OR
            (review_status = 'accepted' AND knowledge_status IS NOT NULL)
        )
    )""",
    """CREATE TABLE fact_evidence (
        id INTEGER PRIMARY KEY,
        fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
        document_text_version_id INTEGER NOT NULL
            REFERENCES document_text_versions(id) ON DELETE RESTRICT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
        normalized_content_hash TEXT NOT NULL,
        normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
        evidence_role TEXT NOT NULL CHECK(evidence_role IN (
            'supports','contradicts','context'
        )),
        excerpt TEXT NOT NULL CHECK(length(excerpt) > 0),
        start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
        end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
        observed_at TEXT,
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        validation_status TEXT NOT NULL DEFAULT 'valid'
            CHECK(validation_status IN ('valid','invalid','stale')),
        invalidated_at TEXT,
        invalidation_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(document_id, normalized_content_hash, normalization_version)
            REFERENCES document_text_versions(
                document_id, normalized_content_hash, normalization_version
            ) ON DELETE RESTRICT,
        CHECK((validation_status = 'valid' AND invalidated_at IS NULL AND invalidation_reason IS NULL)
           OR (validation_status IN ('invalid','stale')
               AND invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL))
    )""",
    """CREATE TABLE fact_relations (
        id INTEGER PRIMARY KEY,
        left_fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
        right_fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
        relation_type TEXT NOT NULL CHECK(relation_type IN (
            'conflicts_with','corroborates','supersedes','refines'
        )),
        confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
        explanation TEXT NOT NULL CHECK(length(trim(explanation)) > 0),
        deterministic_validation_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(deterministic_validation_status IN ('pending','passed','failed')),
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','accepted','rejected')),
        knowledge_status TEXT
            CHECK(knowledge_status IN ('active','disputed','superseded','retracted')),
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        reviewed_at TEXT,
        reviewed_by TEXT,
        review_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(left_fact_id <> right_fact_id),
        CHECK(relation_type NOT IN ('conflicts_with','corroborates')
           OR left_fact_id < right_fact_id),
        CHECK(
            (review_status IN ('pending','rejected') AND knowledge_status IS NULL)
            OR
            (review_status = 'accepted' AND knowledge_status IS NOT NULL)
        ),
        CHECK(review_status <> 'accepted' OR deterministic_validation_status = 'passed')
    )""",
    """CREATE TABLE fact_relation_evidence (
        fact_relation_id INTEGER NOT NULL REFERENCES fact_relations(id) ON DELETE RESTRICT,
        fact_evidence_id INTEGER NOT NULL REFERENCES fact_evidence(id) ON DELETE RESTRICT,
        evidence_role TEXT NOT NULL CHECK(evidence_role IN (
            'supports_relation','challenges_relation','context'
        )),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(fact_relation_id, fact_evidence_id, evidence_role)
    )""",
    """CREATE TABLE entity_merge_events (
        id INTEGER PRIMARY KEY,
        loser_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
        winner_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
        action TEXT NOT NULL CHECK(action IN ('merge','undo')),
        reverses_event_id INTEGER REFERENCES entity_merge_events(id) ON DELETE RESTRICT,
        reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
        actor TEXT NOT NULL CHECK(length(trim(actor)) > 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(loser_entity_id <> winner_entity_id),
        CHECK((action = 'merge' AND reverses_event_id IS NULL)
           OR (action = 'undo' AND reverses_event_id IS NOT NULL))
    )""",
    """CREATE TABLE knowledge_events (
        id INTEGER PRIMARY KEY,
        object_type TEXT NOT NULL CHECK(object_type IN (
            'entity','entity_alias','entity_mention','fact','fact_evidence',
            'fact_relation','entity_merge'
        )),
        object_id INTEGER NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN (
            'created','validated','accepted','rejected','reopened','disputed',
            'superseded','retracted','invalidated','rebuilt','merged','merge_undone'
        )),
        actor_type TEXT NOT NULL CHECK(actor_type IN (
            'deterministic_rule','llm_candidate','human','maintenance_task','system'
        )),
        actor_id TEXT,
        reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
        previous_state_json TEXT,
        new_state_json TEXT,
        details_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(previous_state_json IS NULL OR json_valid(previous_state_json)),
        CHECK(new_state_json IS NULL OR json_valid(new_state_json)),
        CHECK(details_json IS NULL OR json_valid(details_json))
    )""",
    """CREATE TABLE derivation_scopes (
        derivation_id TEXT NOT NULL REFERENCES derivations(id) ON DELETE CASCADE,
        scope_type TEXT NOT NULL CHECK(scope_type IN ('document','entity','fact','fact_relation')),
        scope_id TEXT NOT NULL,
        scope_role TEXT NOT NULL CHECK(scope_role IN ('anchor','input','output','context')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(derivation_id, scope_type, scope_id, scope_role)
    )""",
    """CREATE TABLE job_scopes (
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        scope_type TEXT NOT NULL CHECK(scope_type IN (
            'document','entity','fact','fact_relation','synthesis'
        )),
        scope_id TEXT NOT NULL,
        scope_role TEXT NOT NULL CHECK(scope_role IN ('anchor','input','output','context')),
        ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(job_id, scope_type, scope_id, scope_role)
    )""",
    """CREATE UNIQUE INDEX uq_entity_alias_candidate
       ON entity_aliases(entity_id, alias_kind, namespace, normalized_value)
       WHERE review_status IN ('pending','accepted') AND invalidated_at IS NULL""",
    """CREATE UNIQUE INDEX uq_entity_alias_strong_global
       ON entity_aliases(alias_kind, namespace, normalized_value)
       WHERE is_strong_identifier = 1 AND review_status = 'accepted'
         AND invalidated_at IS NULL""",
    """CREATE UNIQUE INDEX uq_entity_mention_current_span
       ON entity_mentions(document_text_version_id, start_offset, end_offset, surface_text)
       WHERE review_status IN ('pending','accepted') AND invalidated_at IS NULL""",
    """CREATE UNIQUE INDEX uq_fact_live_key ON facts(fact_key)
       WHERE review_status IN ('pending','accepted')""",
    """CREATE UNIQUE INDEX uq_fact_evidence_span
       ON fact_evidence(fact_id, document_text_version_id, start_offset, end_offset, evidence_role)
       WHERE validation_status = 'valid'""",
    """CREATE UNIQUE INDEX uq_fact_relation_symmetric_live
       ON fact_relations(left_fact_id, right_fact_id, relation_type)
       WHERE relation_type IN ('conflicts_with','corroborates')
         AND review_status IN ('pending','accepted')""",
    """CREATE UNIQUE INDEX uq_fact_relation_directed_live
       ON fact_relations(left_fact_id, right_fact_id, relation_type)
       WHERE relation_type IN ('supersedes','refines')
         AND review_status IN ('pending','accepted')""",
    "CREATE UNIQUE INDEX uq_job_scope_ordinal ON job_scopes(job_id, scope_role, ordinal)",
    "CREATE INDEX idx_text_versions_document_current ON document_text_versions(document_id, invalidated_at, normalization_version)",
    "CREATE INDEX idx_entities_review_name ON entities(review_status, entity_type, normalized_name)",
    "CREATE INDEX idx_entities_merged_into ON entities(merged_into_entity_id) WHERE merged_into_entity_id IS NOT NULL",
    "CREATE INDEX idx_entities_derivation ON entities(creation_derivation_id)",
    "CREATE INDEX idx_alias_entity_review ON entity_aliases(entity_id, review_status, invalidated_at)",
    "CREATE INDEX idx_alias_lookup ON entity_aliases(alias_kind, namespace, normalized_value, review_status, invalidated_at)",
    "CREATE INDEX idx_alias_source_document ON entity_aliases(source_document_id)",
    "CREATE INDEX idx_alias_derivation ON entity_aliases(derivation_id)",
    "CREATE INDEX idx_mentions_document ON entity_mentions(document_id, invalidated_at, start_offset)",
    "CREATE INDEX idx_mentions_normalized_hash ON entity_mentions(normalized_content_hash)",
    "CREATE INDEX idx_mentions_normalization_version ON entity_mentions(normalization_version)",
    "CREATE INDEX idx_mentions_entity ON entity_mentions(entity_id, review_status, invalidated_at)",
    "CREATE INDEX idx_mentions_derivation ON entity_mentions(derivation_id)",
    "CREATE INDEX idx_facts_subject_status ON facts(subject_entity_id, review_status, knowledge_status, predicate)",
    """CREATE INDEX idx_facts_object_entity_status
       ON facts(object_entity_id, review_status, knowledge_status, predicate)
       WHERE object_entity_id IS NOT NULL""",
    "CREATE INDEX idx_facts_status ON facts(review_status, knowledge_status, updated_at)",
    "CREATE INDEX idx_facts_predicate_time ON facts(predicate, valid_from, valid_to)",
    "CREATE INDEX idx_facts_derivation ON facts(creation_derivation_id)",
    "CREATE INDEX idx_fact_evidence_fact_valid ON fact_evidence(fact_id, validation_status, document_id)",
    "CREATE INDEX idx_fact_evidence_document ON fact_evidence(document_id, validation_status, start_offset)",
    "CREATE INDEX idx_fact_evidence_normalized_hash ON fact_evidence(normalized_content_hash)",
    "CREATE INDEX idx_fact_evidence_normalization_version ON fact_evidence(normalization_version)",
    "CREATE INDEX idx_fact_evidence_text_version ON fact_evidence(document_text_version_id)",
    "CREATE INDEX idx_fact_evidence_derivation ON fact_evidence(derivation_id)",
    "CREATE INDEX idx_fact_relations_left ON fact_relations(left_fact_id, relation_type, review_status, knowledge_status)",
    "CREATE INDEX idx_fact_relations_right ON fact_relations(right_fact_id, relation_type, review_status, knowledge_status)",
    "CREATE INDEX idx_fact_relations_validation ON fact_relations(relation_type, deterministic_validation_status, review_status)",
    "CREATE INDEX idx_fact_relations_derivation ON fact_relations(derivation_id)",
    "CREATE INDEX idx_relation_evidence_evidence ON fact_relation_evidence(fact_evidence_id)",
    "CREATE INDEX idx_merge_events_loser ON entity_merge_events(loser_entity_id, created_at)",
    "CREATE INDEX idx_merge_events_winner ON entity_merge_events(winner_entity_id, created_at)",
    "CREATE INDEX idx_merge_events_reverses ON entity_merge_events(reverses_event_id)",
    "CREATE INDEX idx_knowledge_events_object ON knowledge_events(object_type, object_id, created_at)",
    "CREATE INDEX idx_knowledge_events_derivation ON knowledge_events(derivation_id) WHERE derivation_id IS NOT NULL",
    "CREATE INDEX idx_knowledge_events_job ON knowledge_events(job_id) WHERE job_id IS NOT NULL",
    """CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events
       BEGIN SELECT RAISE(ABORT, 'knowledge_events_are_append_only'); END""",
    """CREATE TRIGGER knowledge_events_no_delete BEFORE DELETE ON knowledge_events
       BEGIN SELECT RAISE(ABORT, 'knowledge_events_are_append_only'); END""",
    """CREATE TRIGGER facts_no_delete BEFORE DELETE ON facts
       BEGIN SELECT RAISE(ABORT, 'historical_facts_cannot_be_deleted'); END""",
    """CREATE TRIGGER facts_before_publish
       BEFORE UPDATE OF review_status, knowledge_status ON facts
       WHEN NEW.review_status = 'accepted'
       BEGIN
           SELECT CASE WHEN NOT EXISTS (
               SELECT 1 FROM entities
               WHERE id = NEW.subject_entity_id AND review_status = 'accepted'
           ) THEN RAISE(ABORT, 'subject_entity_not_accepted') END;
           SELECT CASE WHEN NEW.object_entity_id IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM entities
               WHERE id = NEW.object_entity_id AND review_status = 'accepted'
           ) THEN RAISE(ABORT, 'object_entity_not_accepted') END;
           SELECT CASE WHEN NOT EXISTS (
               SELECT 1 FROM fact_evidence
               WHERE fact_id = NEW.id AND validation_status = 'valid'
           ) THEN RAISE(ABORT, 'fact_has_no_valid_evidence') END;
       END""",
    """CREATE TRIGGER entities_before_reopen
       BEFORE UPDATE OF review_status ON entities
       WHEN OLD.review_status='accepted' AND NEW.review_status='pending'
       BEGIN
           SELECT CASE WHEN EXISTS (
               SELECT 1 FROM facts
               WHERE review_status='accepted'
                 AND (subject_entity_id=OLD.id OR object_entity_id=OLD.id)
           ) THEN RAISE(ABORT, 'accepted_facts_depend_on_entity') END;
       END""",
    """CREATE TRIGGER facts_before_insert_publish
       BEFORE INSERT ON facts
       WHEN NEW.review_status = 'accepted'
       BEGIN
           SELECT RAISE(ABORT, 'fact_has_no_valid_evidence');
       END""",
    """CREATE TRIGGER fact_relations_before_publish
       BEFORE UPDATE OF review_status, knowledge_status ON fact_relations
       WHEN NEW.review_status = 'accepted'
       BEGIN
           SELECT CASE WHEN NEW.deterministic_validation_status <> 'passed'
               THEN RAISE(ABORT, 'fact_relation_not_validated') END;
           SELECT CASE WHEN NOT EXISTS (
               SELECT 1 FROM fact_relation_evidence AS relation_evidence
               JOIN fact_evidence AS evidence
                 ON evidence.id = relation_evidence.fact_evidence_id
               WHERE relation_evidence.fact_relation_id = NEW.id
                 AND evidence.validation_status = 'valid'
           ) THEN RAISE(ABORT, 'fact_relation_has_no_valid_evidence') END;
       END""",
    """CREATE VIEW canonical_entities AS
       WITH RECURSIVE chain(origin_id, current_id, depth, path) AS (
           SELECT id, id, 0, printf('/%d/', id) FROM entities
           UNION ALL
           SELECT chain.origin_id, entity.merged_into_entity_id, chain.depth + 1,
                  chain.path || printf('%d/', entity.merged_into_entity_id)
           FROM chain JOIN entities AS entity ON entity.id = chain.current_id
           WHERE entity.merged_into_entity_id IS NOT NULL AND chain.depth < 64
             AND instr(chain.path, printf('/%d/', entity.merged_into_entity_id)) = 0
       )
       SELECT chain.origin_id AS entity_id,
              chain.current_id AS canonical_entity_id, chain.depth
       FROM chain JOIN entities AS entity ON entity.id = chain.current_id
       WHERE entity.merged_into_entity_id IS NULL""",
    """CREATE VIEW current_facts AS
       SELECT fact.* FROM facts AS fact
       WHERE fact.review_status = 'accepted'
         AND fact.knowledge_status IN ('active','disputed')
         AND EXISTS (
             SELECT 1 FROM fact_evidence AS evidence
             WHERE evidence.fact_id = fact.id
               AND evidence.validation_status = 'valid'
         )""",
)
