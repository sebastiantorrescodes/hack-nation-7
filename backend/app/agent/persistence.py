"""Checkpoints hold coordination; events and expert answers come from canonical evidence rows."""

from .. import store
from ..events.normalizer import normalize_browser_event
from .schemas import ExpertAnswer, WorkMapState


async def load(session_id: str, expert_name: str):
    rows = (await store._t('apprentice_states').select('state,revision').eq('session_id',session_id).execute()).data
    state = WorkMapState.model_validate(rows[0]['state']) if rows else WorkMapState(expert_name=expert_name)
    events = (await store._t('events').select('payload').eq('session_id',session_id).order('id').execute()).data
    for row in events:
        raw = (row.get('payload') or {}).get('normalized_event')
        if raw:
            event = normalize_browser_event(raw)
            state.events[event.event_id] = event
    turns = (await store._t('transcript_segments').select('*').eq('session_id',session_id).not_.is_('answer_id','null').execute()).data
    state.answers = [ExpertAnswer(id=t['answer_id'], question_id=t['question_id'], event_id=t['linked_event_client_id'],
                                 text=t['text'], segment_id=t['id']) for t in turns if t['speaker']=='expert' and not t['off_record']]
    sessions = (await store._t('sessions').select('capture_phase').eq('id',session_id).execute()).data
    if sessions: state.phase = sessions[0]['capture_phase']
    return state, rows[0]['revision'] if rows else 0


async def save(session, state, revision, *, events=(), turns=()):
    if events or turns:
        raise ValueError('Capture evidence must be ingested before checkpoint reasoning.')
    checkpoint = state.model_dump(mode='json')
    checkpoint['events'], checkpoint['answers'] = {}, []
    links = [answer.model_dump(mode='json') for answer in state.answers if answer.segment_id is not None]
    return (await store._db.rpc('save_capture_checkpoint', {'p_session':session.id,'p_state':checkpoint,
        'p_revision':revision,'p_links':links}).execute()).data
