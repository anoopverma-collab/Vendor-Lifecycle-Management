# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Same reasoning as seed_vendor_kyc_workflow_once.py - editing that
	# patch's own body has no effect on a site that's already past it, so
	# adding a Trashed state/Trash action needs its own new patch. install_
	# vendor_kyc_workflow() is idempotent and self-diffing (see install.py),
	# so re-running it here correctly applies just the delta against the
	# live KYC Workflow, leaving every existing KYC document/state alone.
	from vendor_lifecycle.vendor_lifecycle.install import install_vendor_kyc_workflow

	install_vendor_kyc_workflow()
