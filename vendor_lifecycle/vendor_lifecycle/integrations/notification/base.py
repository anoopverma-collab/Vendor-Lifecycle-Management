# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt


class BaseNotificationHandler:
	"""Interface every deboarding-notification provider must implement.
	Registered via the `vendor_lifecycle_notification_providers` hook, same
	pattern as the e-sign providers in integrations/esign."""

	def notify(self, checklist):
		"""Called when a Vendor Deboarding Checklist is fully completed."""
		raise NotImplementedError
