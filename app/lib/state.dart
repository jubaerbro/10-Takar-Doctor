import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'strings.dart';

/// Everything the whole app needs to know: who is logged in, the server's prices, the language.
class AppState extends ChangeNotifier {
  final Api api = Api();

  Map<String, dynamic>? me;
  Map<String, dynamic>? config;
  String lang = 'bn';
  bool loading = true;
  String? startError;

  Future<void> start() async {
    loading = true;
    startError = null;
    notifyListeners();
    try {
      final prefs = await SharedPreferences.getInstance();
      lang = prefs.getString('lang') ?? 'bn';
      await api.loadToken();
      config = await api.get('/config');
      if (api.token != null) {
        me = await api.get('/me');
      }
    } on ApiException catch (e) {
      if (e.status == 401 || e.status == 403) {
        await api.saveToken(null);
        me = null;
      } else {
        startError = errorText(e);
      }
    }
    loading = false;
    notifyListeners();
  }

  Future<void> setLang(String value) async {
    lang = value;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('lang', value);
  }

  Future<void> loggedIn(String token, Map<String, dynamic> profile) async {
    await api.saveToken(token);
    me = profile;
    notifyListeners();
  }

  Future<void> refreshMe() async {
    me = await api.get('/me');
    notifyListeners();
  }

  void setMe(Map<String, dynamic> profile) {
    me = profile;
    notifyListeners();
  }

  Future<void> logout() async {
    await api.saveToken(null);
    me = null;
    notifyListeners();
  }

  double get balance => ((me?['balance_taka'] ?? 0) as num).toDouble();

  num get callPrice => (config?['instant_call_price_taka'] ?? 10) as num;

  num get callMinutes => (config?['instant_call_max_minutes'] ?? 10) as num;

  num get freeSkipSeconds => (config?['free_skip_seconds'] ?? 3) as num;

  List<String> get specialties =>
      ((config?['specialties'] ?? <dynamic>[]) as List<dynamic>).map((dynamic s) => s.toString()).toList();
}

final AppState appState = AppState();

/// Text in the user's language.
String t(String key) => translate(appState.lang, key);

/// A message for an API error, in the user's language where we have one.
String errorText(Object error) {
  if (error is ApiException) {
    final String key = 'err_${error.code}';
    final String local = t(key);
    return local == key ? error.message : local;
  }
  return t('err_error');
}

String taka(num value) {
  final String number = value == value.roundToDouble() ? value.toInt().toString() : value.toStringAsFixed(2);
  return '৳$number';
}

String clock(int seconds) {
  final int safe = seconds < 0 ? 0 : seconds;
  final String m = (safe ~/ 60).toString();
  final String s = (safe % 60).toString().padLeft(2, '0');
  return '$m:$s';
}

String _two(int n) => n.toString().padLeft(2, '0');

/// A Unix time as the phone's local date and time, for example 12/11/2026 14:30.
String dateText(num unixSeconds) {
  final DateTime d = DateTime.fromMillisecondsSinceEpoch((unixSeconds * 1000).round());
  return '${_two(d.day)}/${_two(d.month)}/${d.year} ${_two(d.hour)}:${_two(d.minute)}';
}

String timeText(num unixSeconds) {
  final DateTime d = DateTime.fromMillisecondsSinceEpoch((unixSeconds * 1000).round());
  return '${_two(d.hour)}:${_two(d.minute)}';
}
