# License decision

The original source code and documentation in this repository are licensed under
the Apache License, Version 2.0. The canonical license text is in the repository
root at [`LICENSE`](../LICENSE).

The Apache-2.0 license applies only to material authored for this repository
unless another file states otherwise. It does not license third-party legal
texts, government datasets, downloaded corpus artifacts, or third-party
dependencies. Those materials remain subject to their respective terms.

The release process must separately review:

1. Code license and contributor expectations.
2. Dependency license compatibility for the locked direct/transitive graph.
3. Attribution and terms for each official source.
4. Whether any prebuilt corpus bundle may be published. Every current core source
   is marked “official public access; redistribution review required,” with no
   affirmative redistribution flag, so the default release downloads sources at
   user setup and publishes no corpus bytes.

The prepared dependency/source inventory is in
[Third-party and source review inventory](Third_Party_and_Source_Review.md). It
narrows the remaining review work but does not approve dependency compatibility,
attribution compliance, or source redistribution.
