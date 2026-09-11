"""Sharing and private transfers price on different bases, and we showed one
flat figure for both regardless of party size — wrong in both directions.

Measured against the live supplier API on 2026-09-11:

- SHARING is per person with a floor. minPax is 2 on most Dubai tours, so a
  solo traveller is billed for two seats. BUT on the normal path the rate call
  is made FOR the party, so the supplier has already multiplied: measured on
  tour 53632/option 222565, sharing rate / pax is a flat 102.30 from pax 2
  through 10. The per-head multiplication only belongs to the
  supplier_priced_for_pax=False path, where the caller holds a true unit price.
- PRIVATE is per vehicle, and `maxPax` is NOT the seat count. Desert Safari
  (30647) and Burj Khalifa (30614) both report `maxPax: 12` while their private
  tier doubles at pax 7 (440 -> 880 AED), so the real vehicle holds 6 and
  maxPax is a two-car booking ceiling. Dividing by maxPax returns one vehicle
  for a party of ten and undercharges by a whole car.
- The supplier applies the vehicle multiple ITSELF: its per-pax rate call steps
  as the party crosses a vehicle boundary, so multiplying again double-charges.
"""

import pytest

from rules import transfer_quote


class TestSharingIsPerPersonWithAMinimum:
    def test_above_the_minimum_bills_actual_pax(self):
        q = transfer_quote(
            tour_rate_inr=20742, pax=10, sharing_price_inr=1772, sharing_min_pax=2,
            supplier_priced_for_pax=False,
        )
        sharing = q["options"][0]
        assert sharing["billed_pax"] == 10
        assert sharing["transfer_total_inr"] == pytest.approx(17720)

    def test_below_the_minimum_bills_the_minimum(self):
        q = transfer_quote(
            tour_rate_inr=2074, pax=1, sharing_price_inr=741, sharing_min_pax=2,
            supplier_priced_for_pax=False,
        )
        sharing = q["options"][0]
        assert sharing["billed_pax"] == 2, "solo traveller must pay the 2-pax floor"
        assert sharing["transfer_total_inr"] == pytest.approx(1482)

    def test_a_supplier_priced_sharing_figure_is_not_multiplied_again(self):
        """The 10x overcharge guard.

        The supplier returned 1,023 AED of sharing transfer for a party of ten
        on tour 53632. Treating that as a per-head unit billed 10,230.
        """
        q = transfer_quote(
            tour_rate_inr=341, pax=10, sharing_price_inr=1023, sharing_min_pax=2
        )
        sharing = q["options"][0]
        assert sharing["transfer_total_inr"] == pytest.approx(1023)
        assert sharing["unit_price_inr"] == pytest.approx(102.30)

    def test_the_supplier_minimum_is_not_re_applied_on_the_priced_path(self):
        # The floor is already inside the figure the supplier returned, so
        # billing it again would charge a solo traveller for four seats.
        q = transfer_quote(
            tour_rate_inr=2074, pax=1, sharing_price_inr=741, sharing_min_pax=2
        )
        assert q["options"][0]["transfer_total_inr"] == pytest.approx(741)

    def test_the_minimum_is_explained_to_the_customer(self):
        # Billed for seats they did not ask for reads as an error unless said.
        q = transfer_quote(
            tour_rate_inr=2074, pax=1, sharing_price_inr=741, sharing_min_pax=2
        )
        assert "minimum" in str(q["options"][0]["note"]).lower()

    def test_no_note_about_minimums_when_it_does_not_apply(self):
        q = transfer_quote(
            tour_rate_inr=2074, pax=4, sharing_price_inr=741, sharing_min_pax=2
        )
        assert "minimum" not in str(q["options"][0]["note"]).lower()

    def test_the_tour_ticket_always_uses_ACTUAL_pax(self):
        # Only the transfer is floored. Charging the ticket at the minimum too
        # would overcharge a solo traveller for a second person's entry.
        q = transfer_quote(
            tour_rate_inr=2074, pax=1, sharing_price_inr=741, sharing_min_pax=2
        )
        assert q["tour_total_inr"] == pytest.approx(2074)


class TestPrivateIsPerVehicle:
    def test_supplier_priced_rate_is_not_multiplied_again(self):
        # The rate call already reflects 2 vehicles for 10 pax; multiplying
        # here would charge for four.
        q = transfer_quote(
            tour_rate_inr=20742, pax=10, private_price_inr=11452, private_max_pax=12
        )
        private = q["options"][0]
        assert private["transfer_total_inr"] == pytest.approx(11452)

    def test_party_size_does_not_change_a_per_vehicle_price(self):
        a = transfer_quote(tour_rate_inr=1000, pax=2, private_price_inr=5334)
        b = transfer_quote(tour_rate_inr=1000, pax=6, private_price_inr=5334)
        assert (a["options"][0]["transfer_total_inr"]
                == b["options"][0]["transfer_total_inr"])

    def test_explicit_seat_count_multiplies_vehicles(self):
        # The caller-supplies-seats path: 10 people, 6 seats -> 2 cars, so
        # passenger 7 adds the second vehicle.
        q = transfer_quote(
            tour_rate_inr=20742, pax=10, private_price_inr=5726,
            private_max_pax=6, supplier_priced_for_pax=False,
        )
        private = q["options"][0]
        assert private["transfer_total_inr"] == pytest.approx(11452)
        assert "x 2" in str(private["note"])

    def test_the_seventh_passenger_triggers_a_second_vehicle(self):
        six = transfer_quote(
            tour_rate_inr=0, pax=6, private_price_inr=440,
            private_max_pax=6, supplier_priced_for_pax=False,
        )["options"][0]["transfer_total_inr"]
        seven = transfer_quote(
            tour_rate_inr=0, pax=7, private_price_inr=440,
            private_max_pax=6, supplier_priced_for_pax=False,
        )["options"][0]["transfer_total_inr"]
        assert six == pytest.approx(440)
        assert seven == pytest.approx(880), "pax 7 must add a second car"

    def test_over_the_booking_ceiling_is_flagged(self):
        q = transfer_quote(
            tour_rate_inr=1000, pax=20, private_price_inr=11452, private_max_pax=12
        )
        assert "ceiling" in str(q["options"][0]["note"]).lower()


class TestComparisonAndSafety:
    def test_cheapest_is_named(self):
        q = transfer_quote(
            tour_rate_inr=20742, pax=10, sharing_price_inr=1772,
            private_price_inr=11452, sharing_min_pax=2, private_max_pax=12,
        )
        # Both figures come from rate calls made for this party of ten, so both
        # are already party totals: 1,772 sharing against 11,452 private.
        assert q["cheapest"] == "Sharing Transfer"
        assert q["options"][0]["total_inr"] < q["options"][1]["total_inr"]

    def test_a_tour_with_only_one_tier_returns_only_that(self):
        q = transfer_quote(tour_rate_inr=1000, pax=2, sharing_price_inr=500)
        assert len(q["options"]) == 1
        assert q["options"][0]["transfer_type"] == "Sharing Transfer"

    def test_no_transfer_prices_yields_no_options_not_a_zero(self):
        # A Rs 0 option would render as "Free" to the customer.
        q = transfer_quote(tour_rate_inr=1000, pax=2)
        assert q["options"] == []
        assert q["cheapest"] is None

    def test_totals_are_ticket_plus_transfer_never_transfer_alone(self):
        q = transfer_quote(
            tour_rate_inr=20742, pax=10, sharing_price_inr=1772, sharing_min_pax=2
        )
        o = q["options"][0]
        assert o["total_inr"] == pytest.approx(
            q["tour_total_inr"] + o["transfer_total_inr"]
        )

    def test_pax_is_floored_at_one(self):
        q = transfer_quote(tour_rate_inr=100, pax=0, private_price_inr=500)
        assert q["pax"] == 1

    def test_instructions_forbid_adding_the_two_together(self):
        q = transfer_quote(
            tour_rate_inr=1000, pax=2, sharing_price_inr=500, private_price_inr=5000
        )
        note = str(q["agent_instructions"]).lower()
        assert "never add the two together" in note
        assert "per vehicle" in note and "per person" in note


class TestAgainstTheLiveSupplierCurve:
    """Pins the measured rate curve of tour 53632 / option 222565, 2026-09-25.

    Both tiers are returned by the supplier ALREADY multiplied for the party
    the rate call was made with, so `transfer_quote` must pass them through
    untouched. Two symmetric ways to get this wrong, both seen in this code:

      - multiplying sharing by pax again -> 10,230 against a real 1,023 (10x
        overcharge);
      - deriving private vehicles from `maxPax` (12) instead of the real
        6-seat vehicle -> one car for a party of ten (undercharge by a car).

    The private column is the proof that the supplier does the multiplying:
    it steps +474.10, exactly one more vehicle base, between pax 6 and 7.
    """

    SHARING = {1: 34.1, 2: 204.6, 5: 511.5, 6: 613.8, 7: 716.1, 10: 1023.0}
    PRIVATE = {1: 474.1, 2: 508.2, 5: 610.5, 6: 644.6, 7: 1118.7, 10: 1221.0}

    @pytest.mark.parametrize("pax", sorted(PRIVATE))
    def test_quote_reproduces_the_supplier_figure(self, pax):
        q = transfer_quote(
            tour_rate_inr=34.1 * pax, pax=pax,
            sharing_price_inr=self.SHARING[pax],
            private_price_inr=self.PRIVATE[pax],
            sharing_min_pax=2, private_max_pax=12,
        )
        by = {o["transfer_type"]: o for o in q["options"]}
        assert by["Private Transfer"]["transfer_total_inr"] == pytest.approx(
            self.PRIVATE[pax]
        ), "private must pass through: the supplier already counted the vehicles"
        assert by["Sharing Transfer"]["transfer_total_inr"] == pytest.approx(
            self.SHARING[pax]
        ), "sharing must pass through: it is a party total, not a per-head unit"

    def test_the_private_tier_steps_by_a_whole_vehicle_at_seven_pax(self):
        # maxPax says 12, but the car holds 6 -- this is the evidence.
        step = self.PRIVATE[7] - self.PRIVATE[6]
        assert step == pytest.approx(474.1, abs=0.5)
