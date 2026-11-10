import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../state.dart';

void showMessage(BuildContext context, String text) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));
}

class LanguageButton extends StatelessWidget {
  const LanguageButton({super.key});

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: () => appState.setLang(appState.lang == 'bn' ? 'en' : 'bn'),
      child: Text(t('language')),
    );
  }
}

class StartErrorScreen extends StatelessWidget {
  const StartErrorScreen({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.cloud_off, size: 56, color: Colors.grey),
              const SizedBox(height: 16),
              Text(message, textAlign: TextAlign.center, style: const TextStyle(fontSize: 16)),
              const SizedBox(height: 24),
              FilledButton(onPressed: appState.start, child: Text(t('retry'))),
            ],
          ),
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- login

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _code = TextEditingController();
  bool _codeSent = false;
  bool _busy = false;
  String _role = 'patient';

  @override
  void dispose() {
    _phone.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _sendCode() async {
    setState(() => _busy = true);
    try {
      final Map<String, dynamic> result =
          await appState.api.post('/auth/otp/request', <String, dynamic>{'phone': _phone.text.trim()});
      if (!mounted) return;
      // A test server returns the code so the app can be tried without SMS. A live server never does.
      final dynamic devCode = result['dev_code'];
      setState(() {
        _codeSent = true;
        _code.text = devCode is String ? devCode : '';
      });
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _verify() async {
    setState(() => _busy = true);
    try {
      final Map<String, dynamic> result = await appState.api.post('/auth/otp/verify', <String, dynamic>{
        'phone': _phone.text.trim(),
        'code': _code.text.trim(),
        'role': _role,
        'language': appState.lang,
      });
      await appState.loggedIn(result['token'] as String, (result['me'] as Map).cast<String, dynamic>());
      return;
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(actions: const <Widget>[LanguageButton()]),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: <Widget>[
            Icon(Icons.medical_services, size: 64, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 16),
            Text(t('app_name'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Text(t('login_title'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 16)),
            const SizedBox(height: 24),
            Wrap(
              alignment: WrapAlignment.center,
              spacing: 8,
              children: <Widget>[
                for (final String role in <String>['patient', 'doctor'])
                  ChoiceChip(
                    label: Text(t('role_$role')),
                    selected: _role == role,
                    onSelected: _codeSent ? null : (bool on) => setState(() => _role = role),
                  ),
              ],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _phone,
              enabled: !_codeSent,
              keyboardType: TextInputType.phone,
              inputFormatters: <TextInputFormatter>[FilteringTextInputFormatter.allow(RegExp(r'[0-9+]'))],
              style: const TextStyle(fontSize: 20),
              decoration: InputDecoration(labelText: t('phone_label'), hintText: t('phone_hint')),
            ),
            const SizedBox(height: 16),
            if (_codeSent) ...<Widget>[
              Text(t('code_sent')),
              const SizedBox(height: 12),
              TextField(
                controller: _code,
                keyboardType: TextInputType.number,
                maxLength: 6,
                inputFormatters: <TextInputFormatter>[FilteringTextInputFormatter.digitsOnly],
                style: const TextStyle(fontSize: 24, letterSpacing: 6),
                decoration: InputDecoration(labelText: t('code_label'), counterText: ''),
              ),
              const SizedBox(height: 16),
              FilledButton(onPressed: _busy ? null : _verify, child: Text(t('verify'))),
              TextButton(
                onPressed: _busy ? null : () => setState(() => _codeSent = false),
                child: Text(t('change_number')),
              ),
            ] else
              FilledButton(onPressed: _busy ? null : _sendCode, child: Text(t('send_code'))),
          ],
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- consent

class ConsentScreen extends StatefulWidget {
  const ConsentScreen({super.key});

  @override
  State<ConsentScreen> createState() => _ConsentScreenState();
}

class _ConsentScreenState extends State<ConsentScreen> {
  bool _busy = false;

  Future<void> _agree() async {
    setState(() => _busy = true);
    try {
      await appState.api.post('/me/consent', <String, dynamic>{'version': appState.me?['terms_version']});
      await appState.refreshMe();
      return;
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    // Doctors agree to the doctor agreement; patients to the patient terms.
    final String prefix = appState.me?['role'] == 'doctor' ? 'd_consent_' : 'consent_';
    return Scaffold(
      appBar: AppBar(title: Text(t('consent_title')), actions: const <Widget>[LanguageButton()]),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            children: <Widget>[
              Expanded(
                child: ListView(
                  children: <Widget>[
                    for (final String key in <String>['${prefix}1', '${prefix}2', '${prefix}3', '${prefix}4'])
                      Padding(
                        padding: const EdgeInsets.only(bottom: 16),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Icon(Icons.check_circle, color: Theme.of(context).colorScheme.primary),
                            const SizedBox(width: 12),
                            Expanded(child: Text(t(key), style: const TextStyle(fontSize: 17))),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
              FilledButton(onPressed: _busy ? null : _agree, child: Text(t('consent_agree'))),
              TextButton(onPressed: appState.logout, child: Text(t('logout'))),
            ],
          ),
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- profile

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final TextEditingController _name = TextEditingController();
  final TextEditingController _age = TextEditingController();
  final TextEditingController _district = TextEditingController();
  final TextEditingController _guardianName = TextEditingController();
  final TextEditingController _guardianRelation = TextEditingController();
  String _gender = 'male';
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _age.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _name.dispose();
    _age.dispose();
    _district.dispose();
    _guardianName.dispose();
    _guardianRelation.dispose();
    super.dispose();
  }

  int? get _ageValue => int.tryParse(_age.text.trim());

  bool get _isMinor {
    final int? age = _ageValue;
    return age != null && age < 18;
  }

  Future<void> _save() async {
    final int? age = _ageValue;
    final bool guardianMissing = _isMinor && (_guardianName.text.trim().isEmpty || _guardianRelation.text.trim().isEmpty);
    if (_name.text.trim().length < 2 || age == null || _district.text.trim().length < 2 || guardianMissing) {
      showMessage(context, t('fill_all'));
      return;
    }
    setState(() => _busy = true);
    try {
      final Map<String, dynamic> me = await appState.api.put('/patient/profile', <String, dynamic>{
        'real_name': _name.text.trim(),
        'age': age,
        'gender': _gender,
        'district': _district.text.trim(),
        if (_isMinor) 'guardian_name': _guardianName.text.trim(),
        if (_isMinor) 'guardian_relation': _guardianRelation.text.trim(),
      });
      appState.setMe(me);
      return;
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(t('profile_title')), actions: const <Widget>[LanguageButton()]),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: <Widget>[
            TextField(controller: _name, decoration: InputDecoration(labelText: t('name'))),
            const SizedBox(height: 16),
            TextField(
              controller: _age,
              keyboardType: TextInputType.number,
              inputFormatters: <TextInputFormatter>[FilteringTextInputFormatter.digitsOnly],
              maxLength: 3,
              decoration: InputDecoration(labelText: t('age'), counterText: ''),
            ),
            const SizedBox(height: 16),
            Text(t('gender')),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: <Widget>[
                for (final String g in <String>['male', 'female', 'other'])
                  ChoiceChip(
                    label: Text(t(g)),
                    selected: _gender == g,
                    onSelected: (bool selected) => setState(() => _gender = g),
                  ),
              ],
            ),
            const SizedBox(height: 16),
            TextField(controller: _district, decoration: InputDecoration(labelText: t('district'))),
            if (_isMinor) ...<Widget>[
              const SizedBox(height: 16),
              Text(t('guardian_note'), style: TextStyle(color: Theme.of(context).colorScheme.primary)),
              const SizedBox(height: 12),
              TextField(controller: _guardianName, decoration: InputDecoration(labelText: t('guardian_name'))),
              const SizedBox(height: 16),
              TextField(controller: _guardianRelation, decoration: InputDecoration(labelText: t('guardian_relation'))),
            ],
            const SizedBox(height: 24),
            FilledButton(onPressed: _busy ? null : _save, child: Text(t('save'))),
          ],
        ),
      ),
    );
  }
}
