# ADR 0001: Python package `oss_lab` instead of top-level `platform/`

**Status:** accepted. A top-level Python package named `platform` would shadow the standard library
module of the same name. Code lives in `src/oss_lab/{core,validation,ai,onboarding}`; the README's
directory names remain the conceptual module map.
