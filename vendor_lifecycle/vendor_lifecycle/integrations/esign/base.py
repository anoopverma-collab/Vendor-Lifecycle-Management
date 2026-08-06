# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt


class BaseESignHandler:
	"""Interface every e-sign provider must implement to plug into Vendor Sign Off.

	A provider is registered by another app (or this one) via the
	`vendor_lifecycle_esign_providers` hook — see hooks.py for the "Manual"
	entry. Core Vendor Lifecycle code only ever calls this interface, never a
	concrete provider, so a new provider can be added with zero core changes.
	"""

	def request(self, sign_off):
		"""Called when a Vendor Sign Off is created. Kick off whatever the
		provider needs to do to get the document in front of the vendor
		(e.g. call an external e-sign API). Manual providers can no-op."""
		raise NotImplementedError

	def confirm_signed(self, sign_off):
		"""Called on submit of a Vendor Sign Off. Must raise (via
		frappe.throw) if the provider cannot confirm the document is
		actually signed — this is what stops a sign-off being submitted
		with nothing behind it."""
		raise NotImplementedError
