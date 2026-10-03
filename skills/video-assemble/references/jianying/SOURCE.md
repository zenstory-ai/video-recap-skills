# JianYing protocol templates

These JSON protocol templates are pinned to
[`duoec/duo-video`](https://github.com/duoec/duo-video) commit
`ef4eb46c823910553f901649f2f13fd7575e748f`, under its MIT license. They are
data/schema baselines, not executable upstream code. Runtime builders deep-copy
the templates and replace authored values such as IDs, paths, timings, and canvas
dimensions. Only the templates the exporter builds from are kept; the nested
compound-draft templates were dropped together with green-screen export.

The required copyright and permission notice is preserved in
`LICENSE.duo-video` in this directory.

The exporter never uses duo-video's embedded example credentials and never
emits resource-ID materials (stickers, effects, transitions, text templates).
