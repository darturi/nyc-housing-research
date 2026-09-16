# License decision required

No repository code license is currently present. The maintainer must choose and
add one before describing the GitHub repository as open source or inviting
third-party redistribution/modification.

The decision should separately review:

1. Code license and contributor expectations.
2. Dependency license compatibility for the locked direct/transitive graph.
3. Attribution and terms for each official source.
4. Whether any prebuilt corpus bundle may be published. Every current core source
   is marked “official public access; redistribution review required,” with no
   affirmative redistribution flag, so the default release downloads sources at
   user setup and publishes no corpus bytes.

Choosing a license is a maintainer/legal decision and was intentionally not
inferred during implementation.

The prepared dependency/source inventory is in
[Third-party and source review inventory](Third_Party_and_Source_Review.md). It
narrows the review work but does not approve compatibility or redistribution.
