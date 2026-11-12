import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Address of the backend. Set at build time:
///   flutter build apk --dart-define=API_BASE_URL=https://api.example.com
const String apiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://10.0.2.2:8000', // the host computer, as seen from the Android emulator
);

class ApiException implements Exception {
  ApiException(this.status, this.code, this.message);

  final int status; // 0 means the server could not be reached
  final String code;
  final String message;

  @override
  String toString() => message;
}

class Api {
  static const String _tokenKey = 'login_token';

  final http.Client _client = http.Client();
  String? token;

  Future<void> loadToken() async {
    final prefs = await SharedPreferences.getInstance();
    token = prefs.getString(_tokenKey);
  }

  Future<void> saveToken(String? value) async {
    token = value;
    final prefs = await SharedPreferences.getInstance();
    if (value == null) {
      await prefs.remove(_tokenKey);
    } else {
      await prefs.setString(_tokenKey, value);
    }
  }

  Future<Map<String, dynamic>> get(String path) => _send('GET', path, null);

  Future<Map<String, dynamic>> post(String path, [Map<String, dynamic>? body]) => _send('POST', path, body);

  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) => _send('PUT', path, body);

  Future<Map<String, dynamic>> delete(String path) => _send('DELETE', path, null);

  /// Send one file with some form fields (used for the doctor's verification documents).
  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    required List<int> bytes,
    required String filename,
    required String mimeType,
  }) {
    final request = http.MultipartRequest('POST', Uri.parse('$apiBaseUrl$path'));
    request.fields.addAll(fields);
    request.files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename, contentType: MediaType.parse(mimeType)));
    return _deliver(request, const Duration(seconds: 90));
  }

  Future<Map<String, dynamic>> _send(String method, String path, Map<String, dynamic>? body) {
    final request = http.Request(method, Uri.parse('$apiBaseUrl$path'));
    request.headers['Content-Type'] = 'application/json';
    if (body != null) {
      request.body = jsonEncode(body);
    }
    return _deliver(request, const Duration(seconds: 20));
  }

  Future<Map<String, dynamic>> _deliver(http.BaseRequest request, Duration timeout) async {
    final currentToken = token;
    if (currentToken != null) {
      request.headers['Authorization'] = 'Bearer $currentToken';
    }

    http.Response response;
    try {
      final streamed = await _client.send(request).timeout(timeout);
      response = await http.Response.fromStream(streamed);
    } catch (_) {
      throw ApiException(0, 'network', 'Could not reach the server.');
    }

    dynamic decoded;
    try {
      decoded = response.bodyBytes.isEmpty ? <String, dynamic>{} : jsonDecode(utf8.decode(response.bodyBytes));
    } catch (_) {
      throw ApiException(response.statusCode, 'bad_response', 'The server sent an unexpected reply.');
    }

    if (response.statusCode >= 400) {
      final dynamic error = decoded is Map ? decoded['error'] : null;
      final String code = error is Map && error['code'] is String ? error['code'] as String : 'error';
      final String message = error is Map && error['message'] is String ? error['message'] as String : 'Something went wrong.';
      throw ApiException(response.statusCode, code, message);
    }
    if (decoded is Map) {
      return decoded.cast<String, dynamic>();
    }
    throw ApiException(response.statusCode, 'bad_response', 'The server sent an unexpected reply.');
  }
}
