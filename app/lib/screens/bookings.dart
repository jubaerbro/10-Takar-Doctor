import 'dart:async';

import 'package:flutter/material.dart';

import '../state.dart';
import '../strings.dart';
import 'onboarding.dart';

double _now() => DateTime.now().millisecondsSinceEpoch / 1000;

String _statusText(String status) {
  final String key = 'st_$status';
  final String text = t(key);
  return text == key ? status : text;
}

// ----------------------------------------------------------------------------- patient: book a doctor

/// Shows a doctor's open times and the price, and books the one the patient picks.
class BookDoctorScreen extends StatefulWidget {
  const BookDoctorScreen({super.key, required this.doctorId});

  final String doctorId;

  @override
  State<BookDoctorScreen> createState() => _BookDoctorScreenState();
}

class _BookDoctorScreenState extends State<BookDoctorScreen> {
  final TextEditingController _problem = TextEditingController();
  Map<String, dynamic>? _offer;
  String? _error;
  String? _slotId;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _problem.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final Map<String, dynamic> offer = await appState.api.get('/doctors/${widget.doctorId}/slots');
      if (mounted) setState(() => _offer = offer);
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  Future<void> _book() async {
    final String? slotId = _slotId;
    if (slotId == null || _problem.text.trim().length < 2) {
      showMessage(context, t('fill_all'));
      return;
    }
    setState(() => _busy = true);
    try {
      await appState.api.post('/bookings', <String, dynamic>{'slot_id': slotId, 'problem': _problem.text.trim()});
      if (!mounted) return;
      showMessage(context, t('book_done'));
      Navigator.of(context).pop(true);
      return;
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
      await _load(); // the time may just have been taken by someone else
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final Map<String, dynamic>? offer = _offer;
    final String? error = _error;
    Widget body;
    if (error != null) {
      body = Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(error, textAlign: TextAlign.center)));
    } else if (offer == null) {
      body = const Center(child: CircularProgressIndicator());
    } else {
      final Map<dynamic, dynamic> doctor = offer['doctor'] as Map<dynamic, dynamic>;
      final List<dynamic> slots = offer['slots'] as List<dynamic>;
      final num price = offer['price_taka'] as num;
      body = Column(
        children: <Widget>[
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: <Widget>[
                Text(doctor['name'].toString(), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
                Text('${designationName(appState.lang, doctor['designation'] as String?)} · ${specialtyName(appState.lang, doctor['specialty'].toString())}'),
                const SizedBox(height: 12),
                Text(
                  t('book_price').replaceAll('{price}', taka(price)).replaceAll('{minutes}', offer['minutes'].toString()),
                  style: const TextStyle(fontSize: 17),
                ),
                const SizedBox(height: 20),
                Text(t('book_pick_time'), style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                if (slots.isEmpty)
                  Text(t('book_no_slots'))
                else
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: <Widget>[
                      for (final dynamic slot in slots)
                        ChoiceChip(
                          label: Text(dateText(slot['start_at'] as num)),
                          selected: _slotId == slot['id'],
                          onSelected: (bool on) => setState(() => _slotId = slot['id'] as String),
                        ),
                    ],
                  ),
                const SizedBox(height: 20),
                TextField(
                  controller: _problem,
                  maxLength: 300,
                  maxLines: 2,
                  decoration: InputDecoration(labelText: t('problem'), hintText: t('problem_hint')),
                ),
                Text(t('book_cancel_rule').replaceAll('{minutes}', offer['free_cancel_minutes'].toString())),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 0, 24, 16),
            child: FilledButton(
              onPressed: _busy || slots.isEmpty ? null : _book,
              child: Text(t('book_confirm').replaceAll('{price}', taka(price))),
            ),
          ),
        ],
      );
    }
    return Scaffold(appBar: AppBar(title: Text(t('book_title'))), body: SafeArea(child: body));
  }
}

// ----------------------------------------------------------------------------- both: my bookings

/// The user's bookings, for a patient or a doctor. Joining happens here; once both sides have joined,
/// the home screen opens the call by itself.
class BookingsScreen extends StatefulWidget {
  const BookingsScreen({super.key});

  @override
  State<BookingsScreen> createState() => _BookingsScreenState();
}

class _BookingsScreenState extends State<BookingsScreen> {
  Timer? _timer;
  List<dynamic>? _upcoming;
  List<dynamic> _past = <dynamic>[];
  bool _busy = false;
  bool _loading = false;

  bool get _isDoctor => appState.me?['role'] == 'doctor';

  @override
  void initState() {
    super.initState();
    _load();
    // Refresh often enough that "Join" appears on time and "waiting for the other person" stays true.
    _timer = Timer.periodic(const Duration(seconds: 5), (Timer timer) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    if (_loading) return;
    _loading = true;
    try {
      final Map<String, dynamic> result = await appState.api.get('/bookings');
      if (mounted) {
        setState(() {
          _upcoming = result['upcoming'] as List<dynamic>;
          _past = result['past'] as List<dynamic>;
        });
      }
    } catch (e) {
      if (mounted && _upcoming == null) showMessage(context, errorText(e));
    } finally {
      _loading = false;
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    await _load();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _join(Map<dynamic, dynamic> b) => _run(() async {
        await appState.api.post('/bookings/${b['id']}/join');
      });

  Future<void> _cancel(Map<dynamic, dynamic> b) async {
    String question = t('booking_cancel_doctor');
    if (!_isDoctor) {
      final bool refund = _now() <= (b['free_cancel_until'] as num).toDouble();
      question = t(refund ? 'booking_cancel_refund' : 'booking_cancel_no_refund');
    }
    final bool? sure = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        content: Text(question),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: Text(t('keep'))),
          TextButton(onPressed: () => Navigator.of(context).pop(true), child: Text(t('yes_cancel'))),
        ],
      ),
    );
    if (sure != true) return;
    await _run(() async {
      await appState.api.post('/bookings/${b['id']}/cancel');
      if (mounted) showMessage(context, t('booking_cancelled'));
    });
  }

  String _who(Map<dynamic, dynamic> b) {
    final dynamic other = _isDoctor ? b['patient'] : b['doctor'];
    if (other is! Map) return '';
    return (_isDoctor ? other['alias'] : other['name']).toString();
  }

  Widget _card(Map<dynamic, dynamic> b, {required bool upcoming}) {
    final num start = b['start_at'] as num;
    final num money = (_isDoctor ? b['pay_taka'] : b['price_taka']) as num;
    final String status = b['status'] as String;
    final bool confirmed = status == 'confirmed';
    final bool canJoin = confirmed && _now() >= (b['join_from'] as num) && _now() < (b['end_at'] as num);
    final bool canCancel = confirmed && _now() < start;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(child: Text(dateText(start), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold))),
                Text(_statusText(status)),
              ],
            ),
            const SizedBox(height: 4),
            Text(_who(b), style: const TextStyle(fontSize: 16)),
            Text(b['problem'].toString()),
            Text(_isDoctor ? t('d_booking_pay').replaceAll('{amount}', taka(money)) : taka(money)),
            if (upcoming && confirmed) ...<Widget>[
              const SizedBox(height: 12),
              if (b['i_joined'] == true)
                Row(
                  children: <Widget>[
                    const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                    const SizedBox(width: 12),
                    Expanded(child: Text(t('booking_waiting_other'))),
                  ],
                )
              else if (canJoin)
                FilledButton(onPressed: _busy ? null : () => _join(b), child: Text(t('booking_join')))
              else
                Text(t('booking_join_from').replaceAll('{time}', timeText(b['join_from'] as num))),
              if (canCancel)
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton(onPressed: _busy ? null : () => _cancel(b), child: Text(t('booking_cancel'))),
                ),
            ],
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final List<dynamic>? upcoming = _upcoming;
    Widget body;
    if (upcoming == null) {
      body = const Center(child: CircularProgressIndicator());
    } else if (upcoming.isEmpty && _past.isEmpty) {
      body = Center(child: Text(t('booking_none')));
    } else {
      body = ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          if (upcoming.isNotEmpty) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(t('booking_upcoming'), style: const TextStyle(fontWeight: FontWeight.w600))),
          for (final dynamic b in upcoming) _card(b as Map<dynamic, dynamic>, upcoming: true),
          if (_past.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8, bottom: 8), child: Text(t('booking_past'), style: const TextStyle(fontWeight: FontWeight.w600))),
          for (final dynamic b in _past) _card(b as Map<dynamic, dynamic>, upcoming: false),
        ],
      );
    }
    return Scaffold(appBar: AppBar(title: Text(t('bookings'))), body: SafeArea(child: body));
  }
}

/// A strip for the home screens: "Your booked call starts at 14:30", with a way to open Bookings.
class NextBookingBanner extends StatelessWidget {
  const NextBookingBanner({super.key, required this.booking, required this.onOpen});

  final Map<String, dynamic> booking;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
      decoration: BoxDecoration(color: Theme.of(context).colorScheme.secondaryContainer, borderRadius: BorderRadius.circular(12)),
      child: Row(
        children: <Widget>[
          const Icon(Icons.event_available),
          const SizedBox(width: 12),
          Expanded(child: Text(t('booking_soon').replaceAll('{time}', dateText(booking['start_at'] as num)))),
          TextButton(onPressed: onOpen, child: Text(t('view'))),
        ],
      ),
    );
  }
}

// ----------------------------------------------------------------------------- doctor: bookable hours

class SlotsScreen extends StatefulWidget {
  const SlotsScreen({super.key});

  @override
  State<SlotsScreen> createState() => _SlotsScreenState();
}

class _SlotsScreenState extends State<SlotsScreen> {
  List<dynamic>? _slots;
  num _minutes = 15;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final Map<String, dynamic> result = await appState.api.get('/doctor/slots');
      if (mounted) {
        setState(() {
          _slots = result['slots'] as List<dynamic>;
          _minutes = result['minutes'] as num;
        });
      }
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    await _load();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _add() async {
    final DateTime today = DateTime.now();
    final DateTime? day = await showDatePicker(
      context: context,
      initialDate: today,
      firstDate: today,
      lastDate: today.add(const Duration(days: 30)),
    );
    if (day == null || !mounted) return;
    final TimeOfDay? time = await showTimePicker(context: context, initialTime: TimeOfDay(hour: (today.hour + 1) % 24, minute: 0));
    if (time == null || !mounted) return;
    final int? count = await showDialog<int>(
      context: context,
      builder: (BuildContext context) => SimpleDialog(
        title: Text(t('slots_how_many')),
        children: <Widget>[
          for (final int n in <int>[1, 2, 4, 8])
            SimpleDialogOption(onPressed: () => Navigator.of(context).pop(n), child: Text('$n × ${_minutes.toString()} min')),
        ],
      ),
    );
    if (count == null) return;
    final double first = DateTime(day.year, day.month, day.day, time.hour, time.minute).millisecondsSinceEpoch / 1000;
    final List<double> starts = <double>[for (int i = 0; i < count; i++) first + i * _minutes * 60];
    await _run(() async {
      await appState.api.post('/doctor/slots', <String, dynamic>{'starts': starts});
    });
  }

  Future<void> _remove(String id) => _run(() async {
        await appState.api.delete('/doctor/slots/$id');
      });

  @override
  Widget build(BuildContext context) {
    final List<dynamic>? slots = _slots;
    return Scaffold(
      appBar: AppBar(title: Text(t('slots_title'))),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _busy ? null : _add,
        icon: const Icon(Icons.add),
        label: Text(t('slots_add')),
      ),
      body: SafeArea(
        child: slots == null
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
                children: <Widget>[
                  Text(t('slots_hint').replaceAll('{minutes}', _minutes.toString())),
                  const SizedBox(height: 12),
                  if (slots.isEmpty) Text(t('slots_none')),
                  for (final dynamic slot in slots)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(slot['status'] == 'booked' ? Icons.event_busy : Icons.event_available),
                      title: Text('${dateText(slot['start_at'] as num)} – ${timeText(slot['end_at'] as num)}'),
                      subtitle: Text(t(slot['status'] == 'booked' ? 'slots_booked' : 'slots_open')),
                      trailing: slot['status'] == 'open'
                          ? TextButton(onPressed: _busy ? null : () => _remove(slot['id'] as String), child: Text(t('slots_remove')))
                          : null,
                    ),
                ],
              ),
      ),
    );
  }
}
