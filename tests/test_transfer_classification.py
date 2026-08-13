"""Shared-vs-private classification for transfers.

Getting this wrong is a pricing error, not a cosmetic one: a Shared product is
priced PER SEAT and a Private one PER VEHICLE, so a shared row mislabelled
Private gets quoted at the whole-vehicle price.

The original check only looked for "shared"/"sharing", so the standard industry
terms (SIC, seat-in-coach, group) would have fallen through to Private. The
supplier returns no shared inventory today, which means this path has never been
exercised against real data — hence these tests.
"""

from __future__ import annotations

import pytest

from parsers import _parse_transfer

_RATES = {"AED": 26.0}


def _option(transfer_type: str = "", vehicle_name: str = "Van"):
    return _parse_transfer(
        {
            "transferType": transfer_type,
            "vehicleName": vehicle_name,
            "vehicleType": "Standard",
            "capacity": 4,
            "luggageCapacity": 4,
            "totalPrice": 100,
            "currencyCode": "AED",
            "uniqueKey": "k",
            "transferID": "i",
            "policyName": "Non refundable",
        },
        _RATES,
        "",
    )


class TestSharedDetection:
    @pytest.mark.parametrize(
        "supplier_value",
        ["Shared", "shared", "Sharing Transfer", "SIC", "sic",
         "Seat-in-Coach", "Seat In Coach", "Group Transfer"],
    )
    def test_shared_variants_are_shared(self, supplier_value: str) -> None:
        assert _option(transfer_type=supplier_value).transfer_type == "Shared"

    @pytest.mark.parametrize(
        "supplier_value",
        ["Standard", "Private Transfer", "Large", "", "Business", "First Class"],
    )
    def test_private_variants_are_private(self, supplier_value: str) -> None:
        assert _option(transfer_type=supplier_value).transfer_type == "Private"

    @pytest.mark.parametrize("vehicle", ["Basic Sedan", "Music Bus", "Classic Van"])
    def test_sic_substring_does_not_false_positive(self, vehicle: str) -> None:
        """'sic' inside Basic/Music/Classic must not read as seat-in-coach."""
        assert _option(vehicle_name=vehicle).transfer_type == "Private"

    def test_shared_detected_from_vehicle_name(self) -> None:
        assert _option(vehicle_name="Shared Shuttle").transfer_type == "Shared"


class TestSupplierTierPreserved:
    """The normalised label used to overwrite the supplier's own tier, losing
    the Standard-vs-Large distinction whenever vehicleType was empty."""

    @pytest.mark.parametrize("tier", ["Standard", "Large", "Private Transfer"])
    def test_tier_survives_normalisation(self, tier: str) -> None:
        opt = _option(transfer_type=tier)
        assert opt.supplier_tier == tier
        assert opt.transfer_type == "Private"  # normalised label still correct

    def test_distinct_tier_appears_in_badges(self) -> None:
        badges = _option(transfer_type="Large").badges
        assert "Private" in badges and "Large" in badges

    def test_redundant_tier_not_duplicated_in_badges(self) -> None:
        badges = _option(transfer_type="Private").badges
        assert badges.count("Private") == 1


def test_cancellation_summary_is_populated() -> None:
    """Declared on the model but never set — always empty before this."""
    assert _option().cancellation_policy_summary == "Non refundable"
