# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe

from vendor_lifecycle.vendor_lifecycle.integrations.esign.base import BaseESignHandler


class ManualESignHandler(BaseESignHandler):
	"""No external account needed — an internal user emails the vendor,
	receives the signed document by hand, and attaches it before submitting."""

	def request(self, sign_off):
		pass

	def confirm_signed(self, sign_off):
		if not sign_off.signed_document:
			frappe.throw(frappe._("Attach the signed document before submitting."))
