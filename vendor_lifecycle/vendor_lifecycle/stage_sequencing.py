# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe

# Order stages happen in when "Enforce Sequential Stages" is on. The second
# item in each tuple is the Settings field that makes that stage skippable —
# None for stages that are never optional (KYC, Sign Off).
STAGE_SEQUENCE = [
	("Vendor KYC", None),
	("Vendor Compliance Audit", "audit_mandatory"),
	("Vendor Sampling Evaluation", "sampling_mandatory"),
	("Vendor Reference Check", "reference_check_mandatory"),
	("Vendor Sign Off", None),
]


def enforce_sequential_creation(doc):
	"""Call from validate() on any stage doctype after Vendor KYC. Requires
	the nearest earlier *mandatory* stage to already be submitted before a
	new document for this stage can be created. A no-op unless Settings has
	"Enforce Sequential Stages" turned on, and unless this doc is linked to
	a Vendor KYC at all."""
	if not doc.is_new():
		return
	if not frappe.db.get_single_value("Vendor Lifecycle Settings", "enforce_sequential_stages"):
		return
	if not doc.kyc:
		return

	names = [name for name, _setting in STAGE_SEQUENCE]
	idx = names.index(doc.doctype)

	for prior_doctype, mandatory_setting in reversed(STAGE_SEQUENCE[:idx]):
		if prior_doctype == "Vendor KYC":
			if not frappe.db.exists("Vendor KYC", {"name": doc.kyc, "docstatus": 1}):
				frappe.throw(frappe._("Vendor KYC must be submitted before starting {0}.").format(doc.doctype))
			return

		if mandatory_setting and not frappe.db.get_single_value("Vendor Lifecycle Settings", mandatory_setting):
			continue  # this stage isn't mandatory — check the one before it instead

		filters = {"kyc": doc.kyc, "docstatus": 1}
		if prior_doctype in ("Vendor Compliance Audit", "Vendor Reference Check"):
			filters["result"] = "Passed"

		if not frappe.db.exists(prior_doctype, filters):
			frappe.throw(
				frappe._("A submitted {0} is required before starting {1}.").format(prior_doctype, doc.doctype)
			)
		return
