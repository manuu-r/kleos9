# Actual CAD parts, exploded views, and manufacturing downloads

The mechanics worker exports the original assembled STEP/STL, separate physical
part STEP/STL files, and `native/outputs/assembly-manifest.json` from the same
generated source. No model call is needed to rebuild saved source and add parts.
The source SHA-256 is recorded and checked against the bytes before execution.

Optional source contract, additive to the existing `assembly` requirement:

```python
assembly = Compound(children=[case_shape, cover_shape])
parts = {
    "case": {"shape": case_shape, "label": "Case", "material": "PLA", "quantity": 1},
    "cover": {"shape": cover_shape, "label": "Cover", "quantity": 1},
}
```

Each physical instance is a real, valid positive-volume solid already in the
assembly. Named IDs remain stable. A quantity greater than one requires that many
separate actual solids at their assembled placements; the exporter emits distinct
instance IDs and quantity one for each. It never creates duplicate geometry to
satisfy a claimed quantity. Existing source without this mapping uses actual
assembly solids with geometry-derived IDs. One solid remains monolithic; the UI
must not invent internal components or imply a meaningful exploded view.

Schema `cad-assembly-v1` uses millimetres and assembled coordinates. The assembly
and every part carry STEP/STL artifact paths and hashes, actual min/max/size/centre
bounds, stable physical identity, and source-declared material metadata. Physical
instance transforms are identity. Exploding is a client-side display translation;
downloaded manufacturing geometry never acquires those translations.

Exports are bounded to 20 parts (43 files including whole STEP/STL and manifest),
below the existing 50-file result contract. Existing file/transfer limits still
apply. A separate native validator checks the complete artifact hash closure,
re-imports every STEP, checks bounds and positive-volume solid validity, and
compares the actual part union with the assembly using symmetric-difference and
overlap volumes. STL bounds must match STEP bounds. The original independent
assembly dimension/clearance checks remain in force. Generated PASS reports do
not supply this validation.

Comparisons fuse the assembly's actual solids first, because OCCT may otherwise
report a false residual for coincident touching faces in a compound. Separate
positive-volume overlap checks still reject intersecting physical parts; touching
surfaces require no gap, displacement or relaxed geometric tolerance.

For comparison only, both sides are regularized solid unions. This avoids an
OCCT compound-subtraction artifact where a touching lid is incorrectly retained
as missing material. Actual positive-volume overlap is still rejected on both
the assembly and exported parts with the same numerical tolerance. The original
separate manufacturing files and their assembled coordinates are preserved.

`ReleasePackaging` preserves exact files referenced by the accepted release and
adds `manufacturing-manifest.json` with part identities, quantities, file paths,
and hashes. Referenced parts must be in the same accepted project/revision file
closure. The archive also retains the source, whole assembly, CAD manifest,
placement reference, and other accepted engineering artifacts. An old release
without a parts manifest stays downloadable; it gains no invented part records.

For releases with a confirmed concept, the package also preserves the exact frozen
`approved-design-reference.json` and its manifest hash. It remains a relative
appearance/layout reference, separate from the assembled millimetre geometry.
See [approved concept constraints](approved-design-reference.md).

Material suitability, slicing, printability, fabrication, packing, delivery and
physical acceptance remain separate gates. Source material text is explicitly
unverified, printability/slicing stay UNKNOWN, and no live production or shipment
is authorized by a preview or a manufacturing download.

`docker/smoke_cad_parts.py` is an offline native-image qualification fixture for
named/legacy part exports, stable legacy IDs, source preservation, assembled
coordinates, and independent geometric part coverage. Run it only in the native
mechanics/validator image, never execute generated CAD on the developer host.
The fixture also includes a circular case and lid touching at a shared face,
and a negative case with real positive-volume penetration; contact passes while
overlap fails.
