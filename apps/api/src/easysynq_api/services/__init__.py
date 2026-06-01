"""Use-case / service layer — orchestrates the domain over the database.

The PEP (``services.authz``) lives here: it gathers grants from the DB, calls the pure
PDP (``domain.authz``), and turns the verdict into an allow + audit hook or a 403/422
(doc 18 §5.2). Routers stay thin; service code is unreachable until the PEP passes.
"""
