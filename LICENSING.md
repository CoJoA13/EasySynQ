# EasySynQ licensing

EasySynQ's original code and documentation are licensed under the
[PolyForm Shield License 1.0.0](LICENSE). This is a source-available license. It permits use,
modification, and distribution for permitted purposes and restricts providing competing products
or services. It is not an OSI-approved open-source license.

This page explains the project owner's intended use cases. The unmodified terms in `LICENSE`
govern the license; this summary does not replace or expand them.

## Internal use

Individuals and organizations may run and modify EasySynQ for their own quality-management work,
including internal use by a commercial business, subject to the license. There is no employee-count
or revenue threshold in Shield.

## Paid consulting

IT consultants may charge for installation, configuration, maintenance, and support of a customer's
own EasySynQ instance, subject to the license. The fee is for their professional services.

This does not grant permission to turn EasySynQ into a competing product or service. Selling a
rebranded EasySynQ product or offering competing hosted EasySynQ access is a different use from
supporting a customer's own deployment and is restricted by Shield's Noncompete terms.

## Competing products and services

Shield's restriction covers competing goods and services, including free offerings. Changing the
name, interface, programming language, or platform does not by itself avoid that restriction.
The license also includes specific New Products and Discontinued Products provisions; read those
terms when assessing a use case.

Shield does not impose an AGPL-style source-offer requirement or require upstream merge requests.
Contributions are welcome through the [contribution guide](CONTRIBUTING.md).

## Third-party materials

Shield applies to the project's original materials. Dependencies, container components, and
third-party assets keep their own licenses, copyright notices, and obligations. In particular,
the bundled Archivo font remains under the
[SIL Open Font License 1.1](apps/web/public/fonts/OFL.txt).
Do not relabel those materials as PolyForm Shield or remove their notices when distributing a build.

## License copies and metadata

The root `LICENSE` is the canonical project copy of the
[official Shield text](https://polyformproject.org/licenses/shield/1.0.0).
Identical copies accompany the API package, web package, contract toolchain, and web public assets.
The web build includes `LICENSE.txt`; the API package includes its license in distribution metadata.
Keep these copies identical when maintaining packaging.

Python and OpenAPI metadata use `LicenseRef-PolyForm-Shield-1.0.0`; npm metadata points to the
package's `LICENSE` file. These labels identify the terms and do not represent OSI approval.

Licensing does not change repository access, grant production-release approval, or waive the
installation and recovery requirements in the [current status](docs/current-status.md).
