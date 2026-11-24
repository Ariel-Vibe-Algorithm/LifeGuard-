# Claude Code – LifeGuard First Aid Supplement 02 (Cumulative)

Add all 45 Hebrew guides from `content/first_aid_supplement_he.json` to the existing LifeGuard offline first-aid search database. This is additive: never replace or delete the original guide library.

## UI requirements
- Do NOT create a long list of emergency topics or category directory as the primary screen.
- Keep one prominent search bar as the main entry point.
- Search fully offline across titles, keywords, synonyms, common misspellings, and context tags (home/street/travel/outdoors).
- Rank by relevance and urgency; open each guide as readable steps with clear warnings.
- Keep emergency call action visible; never claim calls work without cellular coverage.
- Preserve existing UI, users, data, navigation, and functionality.

## Data and safety
- Compare with the original package and app database; merge only non-duplicate guide IDs.
- Preserve sources, warnings, review notes and metadata.
- Mark content `pending_medical_review` until an authorized Israeli reviewer approves it.
- Never label unapproved text official medical guidance.
- Do not add drug dosages, insulin dosing, invasive procedures or advanced rescue instructions for untrained people.
- Preserve Israel-specific Ministry of Health instructions for animal bites/rabies and scorpion stings.
- Do not give blanket honey advice for button-battery ingestion; direct to the Israeli Poison Center/clinicians.
- Distinguish medical first aid from safety procedures (fire, gas, active threat, flash floods, missile alerts).
- Rocket/shelter guidance must use current official Home Front Command content with date/version; do not hardcode variable times or unverified shelters.
- This package contains no images.

## Offline reliability and tests
- Bundle content and search index locally; no network needed to find/open guides.
- Handle corrupt/missing data safely and retain the previous database; version and rollback content.
- Test every guide ID, Hebrew search, synonyms, urgency ranking, duplicate prevention and airplane-mode operation on a real Android device.

## Workflow
1. Audit current schema/search and compare with original package.
2. Report merge plan/conflicts.
3. Merge additively without altering old guides.
4. Run tests and report working, partial, unreviewed and missing items.
Do not redesign the UI into a list. The search bar stays primary.
