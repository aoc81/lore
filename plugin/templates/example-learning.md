---
title: "CI cache key must include the lockfile hash, or builds use stale deps"
date: 2026-01-01
track: bug
category: ci
tags: [ci, cache, dependencies, lockfile, builds, stale]
files: []
status: current
---

> Example entry shipped with the `lore` plugin — delete it once you have real
> learnings. `files: []` keeps the linter quiet; a real entry should list the
> code/config the learning is about (e.g. `.github/workflows/ci.yml`).

## Problem

After a dependency bump, CI kept building with the OLD dependency versions even
though the lockfile had changed. Local builds were correct; only CI was stale.

## Root Cause

The CI cache key was keyed on the OS plus a static string, not on a hash of the
lockfile. CI restored a cache from before the bump and never reinstalled.

## What Didn't Work

Clearing the cache by hand (it came back the next run); pinning versions (the key
was still static, so the stale cache returned).

## Solution

Include a hash of the lockfile in the cache key, e.g. `deps-${os}-${hash(lockfile)}`.
A changed lockfile now yields a new key and a fresh install; an unchanged one still
hits the cache.

## Prevention

Any dependency/build cache key must include a hash of the file that determines its
contents. A cache key that can't change when its inputs change is a stale-cache bug
waiting to happen.
