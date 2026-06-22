// package-loader.js — fetch and lightly validate a canonical Tutor package.
//
// The package format is the "standard" interchange artifact described in
// documents/technical_architecture.md §5. This loader is deliberately
// permissive: it checks the few invariants the frontend relies on and builds
// convenience indexes (concepts/sources by id) so the rest of the app can
// resolve groundings and citations without re-scanning arrays.

const SUPPORTED_MAJOR = 1;

/** Thrown when a package cannot be used by this frontend. */
export class PackageError extends Error {}

/**
 * Fetch and parse a package from `url`.
 * @returns {Promise<TutorPackage>} a wrapped package with helper indexes.
 */
export async function loadPackage(url) {
  let resp;
  try {
    resp = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new PackageError(`Could not fetch package at ${url}: ${e.message}`);
  }
  if (!resp.ok) throw new PackageError(`Package fetch failed (${resp.status}) for ${url}`);

  let raw;
  try {
    raw = await resp.json();
  } catch (e) {
    throw new PackageError(`Package at ${url} is not valid JSON: ${e.message}`);
  }
  return new TutorPackage(raw);
}

/** Wraps a raw package object and exposes resolved lookups. */
export class TutorPackage {
  constructor(raw) {
    if (!raw || typeof raw !== 'object') throw new PackageError('Package is empty or not an object.');

    const major = parseInt(String(raw.schema_version || '0').split('.')[0], 10);
    if (!Number.isFinite(major)) throw new PackageError(`Missing or malformed schema_version: ${raw.schema_version}`);
    if (major > SUPPORTED_MAJOR) {
      throw new PackageError(`Package schema_version ${raw.schema_version} is newer than this Tutor supports (≤ ${SUPPORTED_MAJOR}.x).`);
    }

    if (!Array.isArray(raw.questions) || raw.questions.length === 0) {
      throw new PackageError('Package has no questions[].');
    }

    this.raw = raw;
    this.id = raw.id || 'unknown';
    this.title = raw.title || 'Untitled package';
    this.description = raw.description || '';
    this.questions = raw.questions;

    // id → object indexes for fast resolution.
    this.sourcesById = indexById(raw.sources);
    this.conceptsById = indexById(raw.concepts);
    this.domainsById = indexById(raw.taxonomy && raw.taxonomy.domains);
  }

  get questionCount() { return this.questions.length; }

  concept(id) { return this.conceptsById[id] || null; }
  source(id) { return this.sourcesById[id] || null; }

  /** Concepts a question maps to, resolved to full objects (skips unknown ids). */
  conceptsFor(question) {
    return (question.concept_ids || []).map((id) => this.conceptsById[id]).filter(Boolean);
  }

  /** A human citation string for a question, derived from its source_refs. */
  citationsFor(question) {
    return (question.source_refs || []).map((ref) => {
      const src = this.sourcesById[ref.source_id];
      const title = src ? src.title : ref.source_id;
      return ref.locator ? `${title} (${ref.locator})` : title;
    });
  }
}

function indexById(arr) {
  const out = {};
  if (Array.isArray(arr)) for (const item of arr) if (item && item.id) out[item.id] = item;
  return out;
}
