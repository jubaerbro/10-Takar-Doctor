import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

import '../api.dart';
import '../state.dart';
import '../strings.dart';
import '../video.dart';
import 'home.dart';
import 'onboarding.dart';

const List<String> _docTypes = <String>['bmdc_certificate', 'degree_certificate', 'nid', 'selfie'];

double _nowSeconds() => DateTime.now().millisecondsSinceEpoch / 1000;

// ----------------------------------------------------------------------------- verification

/// The doctor fills in their details, adds photos of their documents and submits for checking.
class DoctorVerificationScreen extends StatefulWidget {
  const DoctorVerificationScreen({super.key});

  @override
  State<DoctorVerificationScreen> createState() => _DoctorVerificationScreenState();
}

class _DoctorVerificationScreenState extends State<DoctorVerificationScreen> {
  final TextEditingController _name = TextEditingController();
  final TextEditingController _bmdc = TextEditingController();
  final TextEditingController _years = TextEditingController();
  final ImagePicker _picker = ImagePicker();
  final Set<String> _languages = <String>{'bn'};
  String _gender = 'male';
  String? _specialty;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    final dynamic profile = appState.me?['profile'];
    if (profile is Map) {
      _name.text = (profile['name'] ?? '').toString();
      _bmdc.text = (profile['bmdc_number'] ?? '').toString();
      _years.text = (profile['experience_years'] ?? '').toString();
      _gender = (profile['gender'] ?? 'male').toString();
      _specialty = profile['specialty']?.toString();
      final dynamic languages = profile['languages'];
      if (languages is List && languages.isNotEmpty) {
        _languages
          ..clear()
          ..addAll(languages.map((dynamic l) => l.toString()));
      }
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _bmdc.dispose();
    _years.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _saveDetails() async {
    final int? years = int.tryParse(_years.text.trim());
    final String? specialty = _specialty;
    if (_name.text.trim().length < 2 || _bmdc.text.trim().length < 2 || years == null || specialty == null || _languages.isEmpty) {
      showMessage(context, t('fill_all'));
      return;
    }
    await _run(() async {
      final Map<String, dynamic> me = await appState.api.put('/doctor/profile', <String, dynamic>{
        'real_name': _name.text.trim(),
        'bmdc_number': _bmdc.text.trim(),
        'gender': _gender,
        'specialty': specialty,
        'experience_years': years,
        'languages': _languages.toList(),
      });
      appState.setMe(me);
      if (mounted) showMessage(context, t('d_details_saved'));
    });
  }

  Future<void> _addDocument(String docType) async {
    final ImageSource? source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (BuildContext context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(
              leading: const Icon(Icons.photo_camera),
              title: Text(t('camera')),
              onTap: () => Navigator.of(context).pop(ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: Text(t('gallery')),
              onTap: () => Navigator.of(context).pop(ImageSource.gallery),
            ),
          ],
        ),
      ),
    );
    if (source == null) return;
    await _run(() async {
      final XFile? photo = await _picker.pickImage(
        source: source,
        imageQuality: 80,
        maxWidth: 2000,
        preferredCameraDevice: docType == 'selfie' ? CameraDevice.front : CameraDevice.rear,
      );
      if (photo == null) return;
      final bool isPng = photo.name.toLowerCase().endsWith('.png');
      await appState.api.upload(
        '/doctor/documents',
        fields: <String, String>{'doc_type': docType},
        bytes: await photo.readAsBytes(),
        filename: isPng ? 'document.png' : 'document.jpg',
        mimeType: isPng ? 'image/png' : 'image/jpeg',
      );
      await appState.refreshMe();
    });
  }

  Future<void> _submit() => _run(() async {
        final Map<String, dynamic> me = await appState.api.post('/doctor/submit');
        appState.setMe(me); // the app now shows the "being checked" screen
      });

  Future<void> _openWallet() async {
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (BuildContext context) => const WalletScreen()));
    try {
      await appState.refreshMe();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: appState,
      builder: (BuildContext context, Widget? child) {
        final String lang = appState.lang;
        final dynamic profile = appState.me?['profile'];
        final bool detailsSaved = profile is Map;
        final Set<String> uploaded = <String>{
          if (profile is Map)
            for (final dynamic d in (profile['documents'] ?? <dynamic>[]) as List<dynamic>) (d as Map)['doc_type'].toString(),
        };
        final String? reviewReason = profile is Map ? profile['review_reason'] as String? : null;
        final num fee = (appState.config?['doctor_registration_fee_taka'] ?? 0) as num;
        final bool allUploaded = _docTypes.every(uploaded.contains);

        return Scaffold(
          appBar: AppBar(
            title: Text(t('d_verify_title')),
            actions: <Widget>[
              const LanguageButton(),
              IconButton(tooltip: t('logout'), onPressed: appState.logout, icon: const Icon(Icons.logout)),
            ],
          ),
          body: SafeArea(
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: <Widget>[
                if (reviewReason != null && reviewReason.isNotEmpty)
                  Container(
                    margin: const EdgeInsets.only(bottom: 20),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(color: Colors.red.shade50, borderRadius: BorderRadius.circular(12)),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(t('d_review_reason'), style: const TextStyle(fontWeight: FontWeight.bold)),
                        const SizedBox(height: 4),
                        Text(reviewReason),
                      ],
                    ),
                  ),
                Text(t('d_details'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                const SizedBox(height: 12),
                TextField(controller: _name, decoration: InputDecoration(labelText: t('name'))),
                const SizedBox(height: 12),
                TextField(controller: _bmdc, decoration: InputDecoration(labelText: t('bmdc_number'))),
                const SizedBox(height: 12),
                TextField(
                  controller: _years,
                  keyboardType: TextInputType.number,
                  inputFormatters: <TextInputFormatter>[FilteringTextInputFormatter.digitsOnly],
                  maxLength: 2,
                  decoration: InputDecoration(labelText: t('experience_years'), counterText: ''),
                ),
                const SizedBox(height: 12),
                Text(t('gender')),
                Wrap(
                  spacing: 8,
                  children: <Widget>[
                    for (final String g in <String>['male', 'female', 'other'])
                      ChoiceChip(label: Text(t(g)), selected: _gender == g, onSelected: (bool on) => setState(() => _gender = g)),
                  ],
                ),
                const SizedBox(height: 12),
                Text(t('specialty')),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: <Widget>[
                    for (final String s in appState.specialties)
                      ChoiceChip(
                        label: Text(specialtyName(lang, s)),
                        selected: _specialty == s,
                        onSelected: (bool on) => setState(() => _specialty = s),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                Text(t('languages_spoken')),
                Wrap(
                  spacing: 8,
                  children: <Widget>[
                    for (final MapEntry<String, String> l in <String, String>{'bn': 'বাংলা', 'en': 'English'}.entries)
                      FilterChip(
                        label: Text(l.value),
                        selected: _languages.contains(l.key),
                        onSelected: (bool on) => setState(() {
                          if (on) {
                            _languages.add(l.key);
                          } else {
                            _languages.remove(l.key);
                          }
                        }),
                      ),
                  ],
                ),
                const SizedBox(height: 16),
                OutlinedButton(
                  style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
                  onPressed: _busy ? null : _saveDetails,
                  child: Text(t('d_save_details')),
                ),
                const SizedBox(height: 28),
                Text(t('d_documents'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                const SizedBox(height: 4),
                Text(t('d_documents_hint')),
                for (final String docType in _docTypes)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      uploaded.contains(docType) ? Icons.check_circle : Icons.radio_button_unchecked,
                      color: uploaded.contains(docType) ? Colors.green.shade700 : Colors.grey,
                    ),
                    title: Text(t('doc_$docType')),
                    trailing: TextButton(
                      onPressed: !detailsSaved || _busy ? null : () => _addDocument(docType),
                      child: Text(t(uploaded.contains(docType) ? 'replace' : 'upload')),
                    ),
                  ),
                if (fee > 0) ...<Widget>[
                  const SizedBox(height: 12),
                  Text(t('d_fee_note').replaceAll('{fee}', taka(fee)).replaceAll('{balance}', taka(appState.balance))),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(onPressed: _openWallet, icon: const Icon(Icons.add), label: Text(t('top_up'))),
                  ),
                ],
                const SizedBox(height: 20),
                if (_busy) const Padding(padding: EdgeInsets.only(bottom: 12), child: LinearProgressIndicator()),
                FilledButton(onPressed: !allUploaded || _busy ? null : _submit, child: Text(t('d_submit'))),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Shown while the verification team checks the documents. Moves on by itself once approved.
class DoctorPendingScreen extends StatefulWidget {
  const DoctorPendingScreen({super.key});

  @override
  State<DoctorPendingScreen> createState() => _DoctorPendingScreenState();
}

class _DoctorPendingScreenState extends State<DoctorPendingScreen> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 10), (Timer timer) async {
      try {
        await appState.refreshMe();
      } catch (_) {}
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(t('d_verify_title')),
        actions: <Widget>[
          const LanguageButton(),
          IconButton(tooltip: t('logout'), onPressed: appState.logout, icon: const Icon(Icons.logout)),
        ],
      ),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(Icons.fact_check, size: 72, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 20),
              Text(t('d_pending_title'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
              const SizedBox(height: 12),
              Text(t('d_pending_body'), textAlign: TextAlign.center),
            ],
          ),
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- doctor home

class DoctorHomeScreen extends StatefulWidget {
  const DoctorHomeScreen({super.key});

  @override
  State<DoctorHomeScreen> createState() => _DoctorHomeScreenState();
}

class _DoctorHomeScreenState extends State<DoctorHomeScreen> {
  Timer? _poller;
  Timer? _heartbeat;
  Map<String, dynamic>? _earnings;
  bool _online = false;
  bool _busy = false;
  bool _callOpen = false;
  bool _checking = false;

  @override
  void initState() {
    super.initState();
    final dynamic profile = appState.me?['profile'];
    _online = profile is Map && profile['online'] == true;
    // While online: look for calls every 2 seconds, and tell the server we are still here every 30.
    _poller = Timer.periodic(const Duration(seconds: 2), (Timer timer) {
      if (_online) _checkForCall();
    });
    _heartbeat = Timer.periodic(const Duration(seconds: 30), (Timer timer) => _beat());
    WidgetsBinding.instance.addPostFrameCallback((Duration _) {
      _checkForCall(); // a call may already be under way if the app was restarted
      _loadEarnings();
      if (_online) _beat();
    });
  }

  @override
  void dispose() {
    _poller?.cancel();
    _heartbeat?.cancel();
    super.dispose();
  }

  Future<void> _beat() async {
    if (!_online) return;
    try {
      final Map<String, dynamic> result = await appState.api.post('/doctor/heartbeat');
      if (mounted && result['online'] != true && _online) {
        setState(() => _online = false);
        if (!_callOpen) showMessage(context, t('d_set_offline'));
      }
    } catch (_) {
      // A missed heartbeat is retried on the next tick.
    }
  }

  Future<void> _checkForCall() async {
    if (_checking || _callOpen || !mounted) return;
    _checking = true;
    try {
      final Map<String, dynamic> result = await appState.api.get('/consultations/current');
      final dynamic current = result['consultation'];
      if (current is Map && mounted && !_callOpen) {
        _openCall(current.cast<String, dynamic>());
      }
    } catch (_) {
      // Tried again two seconds later.
    } finally {
      _checking = false;
    }
  }

  Future<void> _openCall(Map<String, dynamic> consultation) async {
    _callOpen = true;
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (BuildContext context) => DoctorCallScreen(consultation: consultation),
    ));
    _callOpen = false;
    _loadEarnings();
    _beat();
  }

  Future<void> _loadEarnings() async {
    try {
      final Map<String, dynamic> earnings = await appState.api.get('/doctor/earnings');
      if (mounted) setState(() => _earnings = earnings);
    } catch (_) {}
  }

  Future<void> _setOnline(bool online) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await appState.api.post(online ? '/doctor/online' : '/doctor/offline');
      if (mounted) setState(() => _online = online);
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  Widget _earningTile(String label, dynamic data) {
    final Map<dynamic, dynamic> d = data is Map ? data : <dynamic, dynamic>{};
    return Expanded(
      child: Column(
        children: <Widget>[
          Text(label),
          Text(taka((d['earned_taka'] ?? 0) as num), style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          Text(t('d_calls').replaceAll('{calls}', (d['calls'] ?? 0).toString())),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    final Map<String, dynamic>? earnings = _earnings;
    final dynamic profile = appState.me?['profile'];
    return Scaffold(
      appBar: AppBar(
        title: Text(profile is Map ? (profile['name'] ?? '').toString() : t('app_name')),
        actions: <Widget>[
          const LanguageButton(),
          IconButton(tooltip: t('logout'), onPressed: _online ? null : appState.logout, icon: const Icon(Icons.logout)),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: <Widget>[
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: _online ? colors.primaryContainer : Colors.grey.shade200,
                borderRadius: BorderRadius.circular(16),
              ),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(t(_online ? 'd_online' : 'd_offline'), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
                        const SizedBox(height: 4),
                        Text(t(_online ? 'd_online_hint' : 'd_offline_hint')),
                      ],
                    ),
                  ),
                  Switch(value: _online, onChanged: _busy ? null : _setOnline),
                ],
              ),
            ),
            const SizedBox(height: 28),
            Text(t('d_earnings'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            const SizedBox(height: 12),
            if (earnings == null)
              const Center(child: CircularProgressIndicator())
            else ...<Widget>[
              Row(
                children: <Widget>[
                  _earningTile(t('d_today'), earnings['today']),
                  _earningTile(t('d_week'), earnings['last_7_days']),
                  _earningTile(t('d_all_time'), earnings['all_time']),
                ],
              ),
              const SizedBox(height: 16),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.account_balance_wallet),
                title: Text('${t('balance')}: ${taka((earnings['balance_taka'] ?? 0) as num)}'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute<void>(builder: (BuildContext context) => const WalletScreen()));
                  _loadEarnings();
                },
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- doctor's call screen

/// The doctor's view of one consultation: the incoming offer, the call, and the advice note after.
class DoctorCallScreen extends StatefulWidget {
  const DoctorCallScreen({super.key, required this.consultation});

  final Map<String, dynamic> consultation;

  @override
  State<DoctorCallScreen> createState() => _DoctorCallScreenState();
}

class _DoctorCallScreenState extends State<DoctorCallScreen> {
  final TextEditingController _assessment = TextEditingController();
  final TextEditingController _selfCare = TextEditingController();
  final TextEditingController _tests = TextEditingController();
  final TextEditingController _referral = TextEditingController();
  late Map<String, dynamic> _c;
  Timer? _poller;
  Timer? _ticker;
  bool _busy = false;
  bool _polling = false;
  bool _closed = false;

  String get _id => _c['id'] as String;

  String get _status => _c['status'] as String;

  bool get _finished => _status == 'ended' || _status == 'skipped';

  @override
  void initState() {
    super.initState();
    _c = widget.consultation;
    _poller = Timer.periodic(const Duration(seconds: 2), (Timer timer) => _poll());
    _ticker = Timer.periodic(const Duration(seconds: 1), (Timer timer) {
      if (mounted && !_finished) setState(() {});
    });
  }

  @override
  void dispose() {
    _poller?.cancel();
    _ticker?.cancel();
    _assessment.dispose();
    _selfCare.dispose();
    _tests.dispose();
    _referral.dispose();
    super.dispose();
  }

  /// The offer went to someone else, or the patient cancelled: nothing more to do here.
  void _close(String message) {
    if (_closed || !mounted) return;
    _closed = true;
    showMessage(context, message);
    Navigator.of(context).pop();
  }

  Future<void> _poll() async {
    if (_polling || _finished || _closed) return;
    _polling = true;
    try {
      final Map<String, dynamic> latest = await appState.api.get('/consultations/$_id');
      if (mounted) setState(() => _c = latest);
    } on ApiException catch (e) {
      if (e.status == 404) _close(t('d_offer_closed'));
    } finally {
      _polling = false;
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } on ApiException catch (e) {
      if (e.code == 'offer_gone' || e.status == 404) {
        _close(t('d_offer_closed'));
      } else if (mounted) {
        showMessage(context, errorText(e));
      }
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _accept() => _run(() async {
        final Map<String, dynamic> accepted = await appState.api.post('/consultations/$_id/accept');
        if (mounted) setState(() => _c = accepted);
      });

  Future<void> _decline() => _run(() async {
        await appState.api.post('/consultations/$_id/decline');
        if (mounted && !_closed) {
          _closed = true;
          Navigator.of(context).pop();
        }
      });

  Future<void> _end() => _run(() async {
        try {
          final Map<String, dynamic> ended = await appState.api.post('/consultations/$_id/end');
          if (mounted) setState(() => _c = ended);
        } on ApiException catch (e) {
          if (e.code != 'not_active') rethrow; // it had already ended
          final Map<String, dynamic> latest = await appState.api.get('/consultations/$_id');
          if (mounted) setState(() => _c = latest);
        }
      });

  Future<void> _saveAdvice() async {
    if (_assessment.text.trim().length < 2) {
      showMessage(context, t('fill_all'));
      return;
    }
    await _run(() async {
      final Map<String, dynamic> saved = await appState.api.put('/consultations/$_id/advice-note', <String, dynamic>{
        'assessment': _assessment.text.trim(),
        'self_care': _selfCare.text.trim(),
        'tests': _tests.text.trim(),
        'referral': _referral.text.trim(),
      });
      if (mounted) {
        setState(() => _c = saved);
        showMessage(context, t('d_advice_saved'));
      }
    });
  }

  Future<void> _report() async {
    final String? reason = await showModalBottomSheet<String>(
      context: context,
      builder: (BuildContext context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(title: Text(t('report_title_patient'), style: const TextStyle(fontWeight: FontWeight.bold))),
            for (final String r in <String>['abuse', 'inappropriate', 'other'])
              ListTile(title: Text(t('report_$r')), onTap: () => Navigator.of(context).pop(r)),
          ],
        ),
      ),
    );
    if (reason == null) return;
    await _run(() async {
      await appState.api.post('/consultations/$_id/report', <String, dynamic>{'reason': reason});
      if (mounted) showMessage(context, t('report_sent'));
    });
  }

  int _secondsUntil(String field) {
    final dynamic value = _c[field];
    if (value is! num) return 0;
    final int left = (value.toDouble() - _nowSeconds()).ceil();
    return left < 0 ? 0 : left;
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: _finished, // an offer or a call must be answered or ended, not left by the back button
      child: Scaffold(
        appBar: AppBar(automaticallyImplyLeading: _finished, title: Text(t('d_new_call'))),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: switch (_status) {
              'offered' => _offerView(),
              'ready' => _readyView(),
              'active' => _activeView(),
              _ => _finishedView(),
            },
          ),
        ),
      ),
    );
  }

  Widget _patientCard() {
    final dynamic patient = _c['patient'];
    final Map<dynamic, dynamic> p = patient is Map ? patient : <dynamic, dynamic>{};
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text((p['alias'] ?? '').toString(), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        Text(t('d_age_gender').replaceAll('{age}', (p['age'] ?? '').toString()).replaceAll('{gender}', t((p['gender'] ?? 'other').toString()))),
        const SizedBox(height: 12),
        Text(t('problem'), style: const TextStyle(fontWeight: FontWeight.w600)),
        Text((_c['problem'] ?? '').toString(), style: const TextStyle(fontSize: 17)),
      ],
    );
  }

  Widget _offerView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _patientCard(),
        if (_c['from_waiting_request'] == true)
          Container(
            margin: const EdgeInsets.only(top: 16),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: Colors.amber.shade100, borderRadius: BorderRadius.circular(12)),
            child: Row(
              children: <Widget>[
                const Icon(Icons.hourglass_top),
                const SizedBox(width: 12),
                Expanded(child: Text(t('d_waiting_request'))),
              ],
            ),
          ),
        const Spacer(),
        Text(
          t('d_answer_in').replaceAll('{seconds}', _secondsUntil('offer_expires_at').toString()),
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 16),
        FilledButton(
          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(72)),
          onPressed: _busy ? null : _accept,
          child: Text(t('d_accept'), style: const TextStyle(fontSize: 22)),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
          onPressed: _busy ? null : _decline,
          child: Text(t('d_decline')),
        ),
      ],
    );
  }

  Widget _readyView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _patientCard(),
        const Spacer(),
        const Center(child: CircularProgressIndicator()),
        const SizedBox(height: 20),
        Text(t('d_ready_title'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Text(t('d_ready_body').replaceAll('{seconds}', _secondsUntil('join_by').toString()), textAlign: TextAlign.center),
        const Spacer(),
      ],
    );
  }

  Widget _activeView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _patientCard(),
        const SizedBox(height: 16),
        Expanded(
          child: VideoPanel(
            key: ValueKey<String>(_id),
            video: _c['video'] is Map ? (_c['video'] as Map).cast<String, dynamic>() : null,
          ),
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text('${t('time_left')}: ${clock(_secondsUntil('ends_at'))}', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            TextButton.icon(onPressed: _report, icon: const Icon(Icons.flag_outlined), label: Text(t('report'))),
          ],
        ),
        Text(t('d_no_medicine'), style: TextStyle(color: Colors.red.shade700)),
        const SizedBox(height: 8),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: Colors.red.shade700),
          onPressed: _busy ? null : _end,
          child: Text(t('end_call')),
        ),
      ],
    );
  }

  Widget _finishedView() {
    final bool paid = _status == 'ended';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Expanded(
          child: ListView(
            children: <Widget>[
              Text(t('call_ended'), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              Text(paid ? t('d_earned').replaceAll('{amount}', taka((_c['doctor_pay_taka'] ?? 0) as num)) : t('d_not_paid')),
              if (paid) ...<Widget>[
                const SizedBox(height: 24),
                Text(t('d_advice_title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                const SizedBox(height: 4),
                Text(t('d_no_medicine'), style: TextStyle(color: Colors.red.shade700)),
                const SizedBox(height: 12),
                TextField(controller: _assessment, maxLines: 3, maxLength: 1000, decoration: InputDecoration(labelText: t('assessment'))),
                TextField(controller: _selfCare, maxLines: 3, maxLength: 1000, decoration: InputDecoration(labelText: t('self_care'))),
                TextField(controller: _tests, maxLines: 2, maxLength: 1000, decoration: InputDecoration(labelText: t('tests'))),
                TextField(controller: _referral, maxLines: 2, maxLength: 1000, decoration: InputDecoration(labelText: t('referral'))),
                const SizedBox(height: 8),
                FilledButton(onPressed: _busy ? null : _saveAdvice, child: Text(t('d_save_advice'))),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(onPressed: _report, icon: const Icon(Icons.flag_outlined), label: Text(t('report'))),
                ),
              ],
            ],
          ),
        ),
        OutlinedButton(
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56)),
          onPressed: () => Navigator.of(context).pop(),
          child: Text(t('done')),
        ),
      ],
    );
  }
}
