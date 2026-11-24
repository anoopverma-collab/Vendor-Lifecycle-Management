# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

from vendor_lifecycle.setup import add_missing_supplier_status_options


def execute():
	# Existing sites: "Re-Sign Off In Progress", "Compliance Audit Expired"
	# and "Contract Expired" added to Supplier's Vendor Lifecycle Status,
	# unused "Signed" dropped. Fresh installs get the list from after_install.
	add_missing_supplier_status_options()
