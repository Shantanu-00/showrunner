"""Thin wrappers around the Google SDKs — one module per external surface.

Nothing in here knows about Showrunner's domain: a service module owns the call shape, the
region/auth quirks and the transient-vs-permanent classification of that one API, so a worker's
code reads as pipeline logic rather than SDK plumbing. Domain judgment (prompts, rubrics, fusion)
lives with the worker that owns the stage.
"""
