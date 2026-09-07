# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VendorBackgroundCheckReference(Document):
	@frappe.whitelist()
	def load_ratings_from_template(self):
		if not self.rating_template:
			frappe.throw(frappe._("Set a Rating Criteria Template first."))

		template = frappe.get_doc("Rating Criteria Template", self.rating_template)
		self.ratings = []
		for row in template.criteria:
			self.append("ratings", {"criteria": row.criteria})

	def validate(self):
		self._require_background_check_in_draft()
		self._validate_no_duplicate_contact_or_email()
		self._validate_no_duplicate_criteria()
		self._validate_scores()
		self._compute_result()

	def _validate_no_duplicate_criteria(self):
		# A Criteria counted twice (whether picked twice by hand, or via a
		# Template that itself has a duplicate row) silently double-weights
		# it in the average, and double-counts its vote for the pass-count
		# methods — each Criteria should only ever count once per Reference.
		seen = set()
		for row in self.ratings:
			if not row.criteria:
				continue
			if row.criteria in seen:
				frappe.throw(
					frappe._("Row #{0}: Criteria {1} is already used in another row — each Criteria can only appear once.").format(
						row.idx, frappe.bold(row.criteria)
					)
				)
			seen.add(row.criteria)

	def before_submit(self):
		if not self.ratings:
			frappe.throw(frappe._("Add at least one Rating before submitting."))
		for row in self.ratings:
			# A blank score is stored as 0/0.0 (Frappe coerces None to 0 for
			# numeric fields), so 0 is treated the same as "not yet scored"
			# here, not as a real score of zero.
			if not row.score:
				frappe.throw(frappe._("Row #{0}: Score must be filled in before submitting.").format(row.idx))
		self._require_rating_template_still_enabled()
		self._require_criteria_rows_not_disabled()

	def _require_rating_template_still_enabled(self):
		# The Link field's own filter only keeps a disabled template out of
		# the picker going forward — it doesn't stop this Reference from
		# still pointing at one it picked before that template was
		# disabled, so this is checked again here at submit time.
		if self.rating_template and frappe.db.get_value("Rating Criteria Template", self.rating_template, "disabled"):
			frappe.throw(
				frappe._("Rating Criteria Template {0} is disabled — pick a different one before submitting.").format(
					frappe.bold(self.rating_template)
				)
			)

	def _require_criteria_rows_not_disabled(self):
		# The criteria Link field's own filter only keeps a disabled Rating
		# Criteria out of the picker going forward — a row can still be
		# pointing at one that was enabled when picked and got disabled
		# afterward, so this is checked again here, fresh, at submit time
		# (same pattern as _require_rating_template_still_enabled).
		criteria_names = {row.criteria for row in self.ratings if row.criteria}
		if not criteria_names:
			return
		disabled_criteria = set(
			frappe.get_all(
				"Rating Criteria", filters={"name": ["in", list(criteria_names)], "disabled": 1}, pluck="name"
			)
		)
		for row in self.ratings:
			if row.criteria in disabled_criteria:
				frappe.throw(
					frappe._("Row #{0}: Criteria {1} is disabled — pick a different one before submitting.").format(
						row.idx, frappe.bold(row.criteria)
					)
				)

	def before_cancel(self):
		self._require_background_check_in_draft()

	def on_update(self):
		self._recompute_parent()

	def on_cancel(self):
		self._recompute_parent()

	def on_trash(self):
		self._require_background_check_in_draft()
		self._recompute_parent(exclude_self=True)

	def _require_background_check_in_draft(self):
		# References can only be created, edited, or submitted while their
		# Background Check is still a draft — once the Background Check
		# itself is submitted, every Reference under it is permanently
		# locked. The one exception: cancelling a Reference (self.docstatus
		# is already 2 by the time validate()/before_cancel() run, since
		# Frappe flips it before saving) is still allowed once the
		# Background Check has itself been cancelled — otherwise a
		# Reference would be stuck as a permanently-submitted orphan
		# forever, with no way to ever bring it in line with its own
		# now-cancelled parent (see Vendor Background Check.on_cancel,
		# which deliberately does NOT cascade-cancel its References
		# automatically, the same way ERPNext's own Purchase Order doesn't).
		if not self.background_check:
			return
		docstatus = frappe.db.get_value("Vendor Background Check", self.background_check, "docstatus")
		if docstatus == 0:
			return
		if docstatus == 2 and self.docstatus == 2:
			return
		frappe.throw(
			frappe._("{0} is no longer a draft — its References can no longer be changed.").format(
				frappe.bold(self.background_check)
			)
		)

	def _validate_no_duplicate_contact_or_email(self):
		# Scoped to this Reference's own Background Check — the same
		# contact/email can be a legitimate reference for a *different*
		# vendor. A cancelled Reference doesn't count, so its contact/email
		# can be reused freely (e.g. by its own amendment).
		if not self.background_check:
			return
		base_filters = {
			"background_check": self.background_check,
			"docstatus": ["!=", 2],
			"name": ["!=", self.name or ""],
		}
		if self.reference_contact and frappe.db.exists(
			"Vendor Background Check Reference", {**base_filters, "reference_contact": self.reference_contact}
		):
			frappe.throw(
				frappe._("Another Reference under {0} already uses this Contact Number.").format(
					frappe.bold(self.background_check)
				)
			)
		if self.reference_email and frappe.db.exists(
			"Vendor Background Check Reference", {**base_filters, "reference_email": self.reference_email}
		):
			frappe.throw(
				frappe._("Another Reference under {0} already uses this Email.").format(
					frappe.bold(self.background_check)
				)
			)

	def _validate_scores(self):
		# A score of 0/0.0 is treated the same as blank (not yet scored),
		# not as an out-of-range value — Frappe coerces a genuinely blank
		# numeric field to 0 on save, so there's no reliable way to tell
		# "left blank" apart from "typed 0" once saved anyway.
		for row in self.ratings:
			if row.score and not (1 <= row.score <= 5):
				frappe.throw(
					frappe._("Row #{0}: Score must be between 1 and 5.").format(row.idx)
				)

	def _compute_result(self):
		average_rating, result, method, row_results = self._compute_result_values()
		self.average_rating = average_rating
		self.outcome = result
		self.result_method_resolved = method
		# row_results is only populated for the pass-count methods (empty
		# for "Average") — reset every row's Result to blank in that case,
		# so a stale Passed/Failed doesn't linger from before the method
		# was switched to "Average" (the field is hidden then, but the
		# stored value shouldn't stay stale underneath).
		for idx, row in enumerate(self.ratings):
			row.result = row_results[idx] if row_results else None

	def _compute_result_values(self):
		"""Pure computation, no persistence — returns (average_rating,
		result, method, row_results) using the current in-memory ratings
		and whatever threshold/method currently resolve. row_results is a
		list aligned with self.ratings (Passed/Failed/Needs Review per
		row), populated only for the pass-count methods — empty for
		"Average". Shared by _compute_result() (called from validate(),
		which assigns and persists the values) and check_result_is_fresh()
		(which only compares average_rating/result against them, to flag a
		Reference whose committed Result no longer matches what a fresh
		computation would produce, e.g. because Settings changed)."""
		method = self._resolve_result_method()
		if method == "Average":
			average_rating, result = self._compute_average_values()
			row_results = []
		else:
			average_rating, result, row_results = self._compute_pass_count_values(method)
		return average_rating, result, method, row_results

	def _compute_average_values(self):
		# Rows with a blank (or 0/0.0, treated the same as blank) score are
		# excluded from the average rather than counted as a real 0.
		scores = [row.score for row in self.ratings if row.score]
		if not scores:
			return None, "Needs Review"
		average_rating = sum(scores) / len(scores)
		threshold = self._resolve_minimum_rating()
		result = "Passed" if average_rating >= threshold else "Failed"
		return average_rating, result

	def _compute_pass_count_values(self, method):
		# Neither method reduces to a single number, so Average Rating
		# stays blank rather than showing something that didn't actually
		# drive the decision — same convention the Background Check's own
		# aggregate uses for its non-Average methods.
		threshold = self._resolve_minimum_rating()

		row_results = []
		for row in self.ratings:
			if not row.score:
				row_results.append("Needs Review")
			else:
				row_results.append("Passed" if row.score >= threshold else "Failed")

		if not row_results or "Needs Review" in row_results:
			return None, "Needs Review", row_results

		if method == "Each Criteria Must Pass":
			result = "Passed" if all(r == "Passed" for r in row_results) else "Failed"
		else:  # "Majority of Criteria" — a tie counts as Failed.
			passed_count = row_results.count("Passed")
			failed_count = row_results.count("Failed")
			result = "Passed" if passed_count > failed_count else "Failed"
		return None, result, row_results

	def _is_same_result(self, fresh_average_rating, fresh_result, fresh_method, fresh_row_results):
		# Compares every value that can independently go stale — not just
		# the overall Result/Average Rating, but also the resolved method
		# (which controls whether Average Rating even shows on screen) and
		# each row's own Passed/Failed. A method change (e.g. "Each
		# Criteria Must Pass" <-> "Majority of Criteria") or a threshold
		# change can flip one row's Result without flipping the overall
		# Result — e.g. 5 criteria, 4 Passed is still an overall Passed
		# under "Majority of Criteria" even if one of those 4 flips to
		# Failed, so checking only the overall Result would miss that
		# row's Result being stale.
		current_average = round(self.average_rating or 0, 1)
		fresh_average = round(fresh_average_rating or 0, 1)
		if fresh_result != self.outcome or fresh_average != current_average:
			return False
		if fresh_method != self.result_method_resolved:
			return False
		current_row_results = [row.result for row in self.ratings]
		fresh_row_results = fresh_row_results if fresh_row_results else [None] * len(self.ratings)
		return current_row_results == fresh_row_results

	@frappe.whitelist()
	def check_result_is_fresh(self):
		# Deliberately does NOT get re-checked inside before_submit() the
		# way the Background Check's does — this Reference's own
		# validate() always recomputes average_rating/result fresh on
		# every save, including submit, so by the time before_submit()
		# would run, the values are already guaranteed current. This
		# method exists purely so the client can warn the user *before*
		# they submit if the committed Result is about to silently differ
		# from what's currently displayed — a courtesy notice, not a
		# data-integrity backstop (that's already structurally guaranteed
		# here, unlike the Background Check's aggregate).
		fresh_average_rating, fresh_result, fresh_method, fresh_row_results = self._compute_result_values()
		return {
			"is_fresh": self._is_same_result(fresh_average_rating, fresh_result, fresh_method, fresh_row_results)
		}

	def _resolve_result_method(self):
		if self.rating_template:
			template_value = frappe.db.get_value("Rating Criteria Template", self.rating_template, "result_method")
			if template_value:
				return template_value

		return frappe.db.get_single_value("Vendor Lifecycle Settings", "reference_result_method") or "Average"

	def _resolve_minimum_rating(self):
		# The template's own Minimum Average Rating only counts if the
		# template also sets its own Result Method — leaving Result Method
		# blank on the template means "defer to Settings for both", not
		# "defer the method but keep a custom rating" (matches the
		# template's own depends_on, which hides Minimum Average Rating
		# whenever Result Method is blank).
		if self.rating_template:
			template_method = frappe.db.get_value("Rating Criteria Template", self.rating_template, "result_method")
			if template_method:
				template_value = frappe.db.get_value(
					"Rating Criteria Template", self.rating_template, "minimum_average_rating"
				)
				if template_value:
					return template_value

		settings_value = frappe.db.get_single_value("Vendor Lifecycle Settings", "reference_minimum_average_rating")
		if settings_value:
			return settings_value

		frappe.throw(
			frappe._(
				"Set a Minimum Average Rating on the Rating Criteria Template or in Vendor Lifecycle Settings before saving a Reference with ratings filled in."
			)
		)

	def _recompute_parent(self, exclude_self=False):
		# References live as independent documents (a child table row can't
		# have its own nested child table in Frappe — Ratings, which is a
		# table on this doctype, wouldn't persist otherwise), so the parent
		# Background Check's own aggregate Result can't be computed inline
		# during its own validate() the way a normal child table would. It
		# has to be recomputed and pushed in from here instead, whenever a
		# Reference is created, edited, cancelled, or deleted.
		if not self.background_check:
			return

		# recompute_overall_result() loads and re-saves the Background
		# Check — only possible while it's still a draft. A Reference being
		# cancelled/deleted after its own Background Check has already been
		# cancelled (see _require_background_check_in_draft above) has
		# nothing left to push an update into: the Background Check is
		# done and locked, so there's no dashboard/summary state left to
		# keep in sync.
		if frappe.db.get_value("Vendor Background Check", self.background_check, "docstatus") != 0:
			return

		from vendor_lifecycle.vendor_lifecycle.doctype.vendor_background_check.vendor_background_check import (
			recompute_overall_result,
		)

		# on_trash fires before the row is actually removed from the
		# database — without excluding it explicitly here, the rebuilt
		# summary table would still include this about-to-be-deleted
		# Reference, leaving a dangling Link once it's actually gone.
		recompute_overall_result(self.background_check, exclude_name=self.name if exclude_self else None)
