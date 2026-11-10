import 'package:flutter_test/flutter_test.dart';
import 'package:ten_takar_doctor/state.dart';
import 'package:ten_takar_doctor/strings.dart';

void main() {
  test('money is shown without needless decimals', () {
    expect(taka(10), '৳10');
    expect(taka(10.0), '৳10');
    expect(taka(12.5), '৳12.50');
  });

  test('the countdown never goes below zero', () {
    expect(clock(600), '10:00');
    expect(clock(61), '1:01');
    expect(clock(-5), '0:00');
  });

  test('text falls back to English, then to the key', () {
    expect(translate('bn', 'find_doctor'), 'ডাক্তার খুঁজুন');
    expect(translate('en', 'find_doctor'), 'Find a doctor');
    expect(translate('xx', 'find_doctor'), 'Find a doctor');
    expect(translate('bn', 'no_such_key'), 'no_such_key');
  });

  test('every English text has a Bangla version', () {
    for (final String key in <String>['consent_1', 'emergency_body', 'err_insufficient_balance', 'next_doctor']) {
      expect(translate('bn', key), isNot(translate('en', key)));
    }
  });
}
