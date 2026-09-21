import pytest

from dealalerts.parse import clean_link, deal_id, find_discount, parse_fields


def test_clean_link_drops_tracking_and_fragment() -> None:
    dirty = "HTTPS://Shop.Example.com/item/42/?utm_source=x&color=red&fbclid=abc#reviews"
    assert clean_link(dirty) == "https://shop.example.com/item/42?color=red"


def test_deal_id_is_stable_across_tracking_noise() -> None:
    first = deal_id("https://shop.example.com/item/42?utm_source=a")
    second = deal_id("https://shop.example.com/item/42/?utm_campaign=b")
    assert first == second
    assert len(first) == 16


def test_single_us_price_no_discount() -> None:
    fields = parse_fields(
        "2-Pk 1.5m Beats USB-A to USB-C or USB-C Charging Cables (Black) $14", "", "us"
    )
    assert (fields.price_now, fields.currency) == (14.0, "USD")
    assert fields.usual_price is None
    assert fields.discount_pct is None


def test_singapore_usual_price_gives_discount_and_shop() -> None:
    fields = parse_fields("Sony WH-1000XM6 S$89 (U.P. S$549) at Amazon.sg", "", "sg")
    assert (fields.price_now, fields.usual_price, fields.currency) == (89.0, 549.0, "SGD")
    assert fields.discount_pct == pytest.approx(83.8, abs=0.1)
    assert fields.shop == "Amazon.sg"


def test_bare_dollar_follows_region() -> None:
    assert parse_fields("McDonald's S'pore $1 Coffee Deal", "", "sg").currency == "SGD"
    assert parse_fields("爭鮮迴轉店：堂食$34產品", "", "hk").currency == "HKD"


def test_was_price_in_us_title() -> None:
    fields = parse_fields("Dyson V8 Cordless Vacuum $199.99 (was $449.99)", "", "us")
    assert fields.discount_pct == pytest.approx(55.6, abs=0.1)


def test_japanese_yen_price_with_thousands_comma() -> None:
    fields = parse_fields("【1,073円】ポッカサッポロ じっくりコトコト 3食入り×5個がセール特価", "", "jp")
    assert (fields.price_now, fields.currency) == (1073.0, "JPY")


def test_japanese_points_back_is_not_a_discount() -> None:
    fields = parse_fields("【実質436円】マンチキン ミラクルカップが70％ポイント還元", "", "jp")
    assert fields.price_now == 436.0
    assert fields.discount_pct is None


def test_german_prices_with_decimal_comma_and_statt() -> None:
    fields = parse_fields("Preisfehler? Bosch Akkuschrauber 29,99 € statt 129,99 €", "", "eu")
    assert (fields.price_now, fields.usual_price, fields.currency) == (29.99, 129.99, "EUR")
    assert fields.discount_pct == pytest.approx(76.9, abs=0.1)


def test_mixed_currencies_never_give_a_discount() -> None:
    fields = parse_fields("Gadget US$50 (was S$200)", "", "sg")
    assert fields.discount_pct is None


def test_leading_shop_with_fullwidth_colon() -> None:
    assert parse_fields("宜家 IKEA：美食站 本月精選", "", "hk").shop == "宜家 IKEA"


def test_shop_after_at() -> None:
    assert parse_fields("Up to 70% off sitewide at Nike", "", "us").shop == "Nike"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Anker charger 70% off", 70.0),
        ("Save 80% on winter coats", 80.0),
        ("Lego set -72%", 72.0),
        ("全場3折", 70.0),
        ("指定貨品85折", 15.0),
        ("お弁当が半額", 50.0),
        ("全品7割引", 70.0),
        ("Extra 10% off, already 75% off", 75.0),
    ],
)
def test_stated_discounts(text: str, expected: float) -> None:
    assert find_discount(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    [
        "Up to 70% off sitewide at Nike",
        "Bis zu 80% Rabatt im Sale",
        "10-70% off everything",
        "低至3折",
        "3折起",
        "最大半額",
        "70％ポイント還元",
        "90% cashback for new users",
        "Bank of China Offers Up to 1.8% Time Deposit Promo Rates",
        "CIMB fixed deposit now pays 1.85% p.a.",
        "Battery lasts 80% longer",
    ],
)
def test_false_alarm_wording_gives_no_discount(text: str) -> None:
    assert find_discount(text) is None


def test_summary_is_used_when_title_has_no_price() -> None:
    fields = parse_fields("Crazy headphone deal", "Now $20 (reg. $100) while stock lasts", "us")
    assert (fields.price_now, fields.usual_price) == (20.0, 100.0)
    assert fields.discount_pct == pytest.approx(80.0)


@pytest.mark.parametrize(
    "text",
    [
        # A dash that does not touch the number is not a price cut.
        "128GB - 85% battery health",
        # "Up to" with a word in between is still "up to".
        "Up to an extra 70% off",
        "up to an additional 60% off",
        # The percentage applies to something other than this purchase.
        "70% off second item",
        "70% off 2nd item",
        "70% off shipping",
        "70% off delivery",
        "75% off your first month",
        "50% off your next order",
    ],
)
def test_conditional_and_non_price_percentages_are_not_discounts(text: str) -> None:
    assert find_discount(text) is None
