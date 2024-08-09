# GitHub Readme Stats Cache

### Description

Sanic app that proxies and caches responses from my
[GitHub Readme Stats](https://github.com/tuokri/github-readme-stats)
fork deployed on Vercel.

### Why?

When GitHub Readme Stats is deployed with a free Vercel
plan, it seems to timeout sometimes, causing broken images in my
GitHub profile. This is solved using a proxy cache with a task
queue (Celery).

---

### Development TODOs

- Replace Celery with taskiq?
