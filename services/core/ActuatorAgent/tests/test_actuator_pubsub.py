# pytest test cases for Actuator agent
from datetime import datetime, timedelta

import gevent
import gevent.subprocess as subprocess
import pytest
import types
from gevent.subprocess import Popen
from mock import MagicMock
from volttron.platform.messaging import topics

FAILURE = 'FAILURE'

SUCCESS = 'SUCCESS'
PLATFORM_ACTUATOR = 'platform.actuator'
TEST_AGENT = 'test-agent'
actuator_uuid = None


@pytest.fixture(scope="module")
def publish_agent(request, volttron_instance1):
    global actuator_uuid
    # Create master driver config and 4 fake devices each with 6 points
    process = Popen(['python', 'config_builder.py', '--count=4', '--publish-only-depth-all',
                     'fake', 'fake6.csv', 'null'], env=volttron_instance1.env, cwd='scripts/scalability-testing',
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = process.wait()
    print result
    assert result == 0

    # Start the master driver agent which would intern start the fake driver using the configs created above
    master_uuid = volttron_instance1.install_agent(
        agent_dir="services/core/MasterDriverAgent",
        config_file="scripts/scalability-testing/configs/master-driver.agent",
        start=True)
    print("agent id: ", master_uuid)
    gevent.sleep(2)  # wait for the agent to start and start the devices

    # Start the actuator agent through which publish agent should communicate to fake device
    # Start the master driver agent which would intern start the fake driver using the configs created above
    actuator_uuid = volttron_instance1.install_agent(
        agent_dir="services/core/ActuatorAgent",
        config_file="services/core/ActuatorAgent/tests/actuator.config",
        start=True)
    print("agent id: ", actuator_uuid)

    listener_uuid = volttron_instance1.install_agent(
        agent_dir="examples/ListenerAgent",
        config_file="examples/ListenerAgent/config",
        start=True)
    print("agent id: ", listener_uuid)

    # 3: Start a fake agent to publish to message bus
    fake_publish_agent = volttron_instance1.build_agent()

    # attach actuate method to fake_publish_agent as it needs to be a class method
    # for the call back to work
    # fake_publish_agent.callback = types.MethodType(callback, fake_publish_agent)

    # 4: add a tear down method to stop sqlhistorian agent and the fake agent that published to message bus
    def stop_agent():
        print("In teardown method of module")
        volttron_instance1.stop_agent(actuator_uuid)
        volttron_instance1.stop_agent(master_uuid)
        fake_publish_agent.core.stop()

    request.addfinalizer(stop_agent)
    return fake_publish_agent


# def callback(self, peer, sender, bus, topic, headers, message):
#     print("*************In callback")
#     print ("topic:", topic, 'header:', headers, 'message:', message)


@pytest.mark.actuator_pubsub
def test_schedule_response(publish_agent):
    """
    Test requesting a new schedule and canceling a schedule through pubsub
    Format of expected result
    Expected Header
    {
    'type': <'NEW_SCHEDULE', 'CANCEL_SCHEDULE'>
    'requesterID': <Agent ID from the request>,
    'taskID': <Task ID from the request>
    }
    Expected message
    {
    'result': <'SUCCESS', 'FAILURE', 'PREEMPTED'>,
    'info': <Failure reason, if any>,
    'data': <Data about the failure or cancellation, if any>
    }
    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    # Mock callback methods
    print ("**** test_schedule_response ****")
    global actuator_uuid
    publish_agent.callback = MagicMock(name="callback")

    # subscribe to schedule response topic
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)

    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_schedule_response',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW',  # ('HIGH, 'LOW', 'LOW_PREEMPT').
    }
    msg = [
        ['fakedriver0', start, end]
    ]

    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    gevent.sleep(1)
    assert publish_agent.callback.call_count == 1
    print ('call args ', publish_agent.callback.call_args[0][1])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['taskID'] == 'task_schedule_response'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == SUCCESS

    # Test valid cancellation
    header = {
        'type': 'CANCEL_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_schedule_response'  # unique (to all tasks) ID for scheduled task.
    }
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    print ("after cancel request")
    assert publish_agent.callback.call_count == 2
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['taskID'] == 'task_schedule_response'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == SUCCESS
    assert result_header['type'] == 'CANCEL_SCHEDULE'


@pytest.mark.actuator_pubsub
def test_schedule_announce(publish_agent, volttron_instance1):
    """
    Tests the schedule announcements of actuator. waits for two announcements and checks if the right parameters
    are sent to call back method.
    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    :param volttron_instance1: Volttron instance on which test is run
    """
    print ("**** test_schedule_announce ****")
    global actuator_uuid
    # Use a actuator that publishes frequently
    volttron_instance1.stop_agent(actuator_uuid)
    actuator_uuid = volttron_instance1.install_agent(
        agent_dir="services/core/ActuatorAgent",
        config_file="services/core/ActuatorAgent/tests/actuator2.config",
        start=True)
    try:
        publish_agent.actuate0 = MagicMock(name="magic_actuate0")
        announce = topics.ACTUATOR_SCHEDULE_ANNOUNCE(campus='', building='', unit='fakedriver0')
        publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                           prefix=announce,
                                           callback=publish_agent.actuate0).get()

        start = str(datetime.now() + timedelta(seconds=1))
        end = str(datetime.now() + timedelta(seconds=6))
        print ('start time for device0', start)

        msg = [
            ['fakedriver0', start, end]
        ]

        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_new_schedule',
            TEST_AGENT,
            'task_schedule_announce',
            'LOW',
            msg).get(timeout=10)
        # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
        assert result['result'] == 'SUCCESS'
        gevent.sleep(5)
        assert publish_agent.actuate0.called == True
        assert publish_agent.actuate0.call_count == 2
        args_list1 = publish_agent.actuate0.call_args_list[0][0]
        args_list2 = publish_agent.actuate0.call_args_list[1][0]
        assert args_list1[1] == args_list2[1] == 'platform.actuator'
        assert args_list1[3] == args_list2[3] == 'devices/actuators/schedule/announce/fakedriver0'
        assert args_list1[4]['taskID'] == args_list2[4]['taskID'] == 'task_schedule_announce'
        assert args_list1[4]['requesterID'] == args_list2[4]['requesterID'] == TEST_AGENT
        datetime1 = datetime.strptime(args_list1[4]['time'], '%Y-%m-%d %H:%M:%S')
        datetime2 = datetime.strptime(args_list2[4]['time'], '%Y-%m-%d %H:%M:%S')
        delta = datetime2 - datetime1
        assert delta.seconds == 2

    finally:
        # cancel so fakedriver0 can be used by other tests
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_schedule_announce').get(timeout=10)
        volttron_instance1.stop_agent(actuator_uuid)
        print ("creating instance of actuator with larger publish frequency")
        actuator_uuid = volttron_instance1.install_agent(
            agent_dir="services/core/ActuatorAgent",
            config_file="services/core/ActuatorAgent/tests/actuator.config",
            start=True)


@pytest.mark.actuator_pubsub
def test_schedule_int_agentid(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test Agent=None

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_int_agentid ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': [1234],  # The name of the requesting agent.
        'taskID': 'task_schedule_int_agent',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'
    }
    msg = [
        ['fakedriver1', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    try:
        assert publish_agent.callback.call_count == 1
        print (publish_agent.callback.call_args[0])
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['type'] == 'NEW_SCHEDULE'
        assert result_header['taskID'] == 'task_schedule_int_agent'
        assert result_message['result'] == SUCCESS
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            [1234],
            'task_schedule_int_agent').get(timeout=10)
        print ("result of cancel ", result)
        gevent.sleep(1)


@pytest.mark.actuator_pubsub
def test_schedule_int_taskid(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test valid task id

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_int_taskid ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,
        'priority': 'LOW',
        'taskID': 1234  # unique (to all tasks) ID for scheduled task
    }
    msg = [
        ['fakedriver1', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    print ('call args list:', publish_agent.callback.call_args_list)
    gevent.sleep(1)
    try:
        print ("call args list : ", publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['type'] == 'NEW_SCHEDULE'
        assert result_header['requesterID'] == TEST_AGENT
        assert result_message['result'] == SUCCESS
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            1234).get(timeout=10)
        print ("result of final cancel ", result)


# TODO: Remove this test case once the code is fixed.
# Ideally the first subscription itself should fail and hence this test case would be irrelevant
@pytest.mark.actuator_pubsub
def test_schedule_conflict_previous_agentid_array(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test for conflict message when the
    request id of an existing task (previously scheduled) has agentid=[int]

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_conflict_previous_agentid_array ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ("Schedule first with array requester id")
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': [1234],  # The name of the requesting agent.
        'taskID': 'task_schedule_int_agent',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'
    }
    msg = [
        ['fakedriver1', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    publish_agent.callback.reset_mock()

    print ("now schedule again. Expecting a conflict")
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,
        'priority': 'LOW',
        'taskID': 1234  # unique (to all tasks) ID for scheduled task
    }
    msg = [
        ['fakedriver1', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    print ('call args list:', publish_agent.callback.call_args_list)
    gevent.sleep(1)
    try:
        print ("call args list : ", publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['type'] == 'NEW_SCHEDULE'
        assert result_header['requesterID'] == TEST_AGENT
        assert result_message['result'] == FAILURE
        assert result_message['info'] == "MALFORMED_REQUEST: TypeError: unhashable type: 'list'"
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            1234).get(timeout=10)
        print ("result of final cancel ", result)


@pytest.mark.actuator_pubsub
def test_schedule_empty_agent(publish_agent):
    """
    Test responses for schedule request through pubsub. Test Agent=''. This test case should be removed
    once agent id are generated by the volttron platform

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_empty_agent ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=1))
    end = str(datetime.now() + timedelta(seconds=2))
    print ('start time for device1', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': '',  # The name of the requesting agent.
        'taskID': 'task_empty_agent_id',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'
    }
    msg = [
        ['fakedriver1', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    try:
        print ('call args list:', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        print (publish_agent.callback.call_args[0])
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['type'] == 'NEW_SCHEDULE'
        assert result_header['taskID'] == 'task_empty_agent_id'
        assert result_message['result'] == SUCCESS
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_empty_agent_id').get(timeout=10)


@pytest.mark.actuator_pubsub
def test_schedule_error_invalid_type(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test invalid type in header

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_invalid_type ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")

    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)

    header = {
        'type': 'NEW_SCHEDULE2',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task1',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'  # ('HIGH, 'LOW', 'LOW_PREEMPT').
    }
    msg = [
        ['fakedriver0', start, end]
    ]

    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print ('call args ', publish_agent.callback.call_args[0][1])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['type'] == 'NEW_SCHEDULE2'
    assert result_header['taskID'] == 'task1'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'INVALID_REQUEST_TYPE'


@pytest.mark.actuator_pubsub
def test_schedule_error_invalid_task(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test invalid task id

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_invalid_task ****")
    # Mock callback method
    publish_agent.callback = MagicMock(name="callback")

    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'CANCEL_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_invalid_task'  # unique (to all tasks) ID for scheduled task.
    }
    msg = [
        ['fakedriver0', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'TASK_ID_DOES_NOT_EXIST'
    assert result_header['type'] == 'CANCEL_SCHEDULE'


@pytest.mark.actuator_pubsub
def test_schedule_error_none_taskid(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test taskID=None

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_none_taskid ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,
        'priority': 'LOW'
    }
    msg = [
        ['fakedriver0', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    print ('call args list:', publish_agent.callback.call_args_list)
    gevent.sleep(1)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]

    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'MISSING_TASK_ID'


@pytest.mark.actuator_pubsub
def test_schedule_error_none_agent(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test Agent=None

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_none_agent ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        # 'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_schedule_response-1',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'
    }
    msg = [
        ['fakedriver0', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['taskID'] == 'task_schedule_response-1'
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'MISSING_AGENT_ID'


@pytest.mark.actuator_pubsub
def test_schedule_error_intarray_taskid(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test taskid=[int]

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_intarray_taskid ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,
        'priority': 'LOW',
        'taskID': [1234]  # unique (to all tasks) ID for scheduled task
    }
    msg = [
        ['fakedriver0', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    print ('call args list:', publish_agent.callback.call_args_list)
    gevent.sleep(1)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]

    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == FAILURE
    assert result_message['info'] == "MALFORMED_REQUEST: TypeError: unhashable type: 'list'"


@pytest.mark.actuator_pubsub
def test_schedule_error_empty_message(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test empty message

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_empty_message ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_empty_message',
        'priority': 'LOW'
    }
    msg = [

    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['taskID'] == 'task_empty_message'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'MALFORMED_REQUEST_EMPTY'


@pytest.mark.actuator_pubsub
def test_schedule_error_multiple_missing_headers(publish_agent):
    """
    Test error responses for schedule request through pubsub. Test multiple mising headers

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_multiple_missing_headers ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")

    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_schedule_response-1'
        # 'priority': 'LOW'
    }
    msg = [

    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()

    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['taskID'] == 'task_schedule_response-1'
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'MALFORMED_REQUEST_EMPTY' or result_message['info'] == 'MISSING_PRIORITY'


@pytest.mark.actuator_pubsub
def test_schedule_error_duplicate_task(publish_agent):
    """
    Test error response for schedule request through pubsub. Test Agent=''. This test case should be removed
    once agent id are generated by the volttron platform

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_duplicate_task ****")

    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")

    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=4))
    print ('start time for device0', start)
    msg = [
        ['fakedriver0', start, end]
    ]

    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_duplicate_task',
        'LOW',
        msg).get(timeout=10)
    assert result['result'] == 'SUCCESS'
    print ("Result of schedule through rpc ", result)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_duplicate_task',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'
    }
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    try:
        print ('call args list:', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 2  # once for rpc call and once for publish
        print (publish_agent.callback.call_args[0])
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['type'] == 'NEW_SCHEDULE'
        assert result_header['taskID'] == 'task_duplicate_task'
        assert result_message['result'] == FAILURE
        assert result_message['info'] == 'TASK_ID_ALREADY_EXISTS'
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_duplicate_task').get(timeout=10)


@pytest.mark.actuator_pubsub
def test_schedule_error_missing_priority(publish_agent):
    """
    Test error response for schedule request through pubsub. Test missing priority info

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_missing_priority ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_missing_priority'  # unique (to all tasks) ID for scheduled task.
        # 'priority': 'LOW'
    }
    msg = [
        ['fakedriver0', start, end]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['taskID'] == 'task_missing_priority'
    assert result_message['result'] == FAILURE
    assert result_message['info'] == 'MISSING_PRIORITY'


@pytest.mark.actuator_pubsub
def test_schedule_error_malformed_request(publish_agent):
    """
    Test error response for schedule request through pubsub.
    Test malformed request by sending a message without end date.

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_schedule_error_malformed_request ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    publish_agent.callback.reset_mock()
    # subscribe to schedule response topic
    print ('topic scheule response is :', topics.ACTUATOR_SCHEDULE_RESULT)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=topics.ACTUATOR_SCHEDULE_RESULT,
                                       callback=publish_agent.callback).get()

    start = str(datetime.now() + timedelta(seconds=10))
    end = str(datetime.now() + timedelta(seconds=20))
    print ('start time for device0', start)
    header = {
        'type': 'NEW_SCHEDULE',
        'requesterID': TEST_AGENT,  # The name of the requesting agent.
        'taskID': 'task_schedule_response-1',  # unique (to all tasks) ID for scheduled task.
        'priority': 'LOW'
    }
    msg = [
        ['fakedriver0', start]
    ]
    publish_agent.vip.pubsub.publish(peer='pubsub', topic=topics.ACTUATOR_SCHEDULE_REQUEST, headers=header,
                                     message=msg).get()
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print (publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == topics.ACTUATOR_SCHEDULE_RESULT
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['type'] == 'NEW_SCHEDULE'
    assert result_header['taskID'] == 'task_schedule_response-1'
    assert result_message['result'] == FAILURE
    assert result_message['info'].startswith('MALFORMED_REQUEST')


@pytest.mark.actuator_pubsub
def test_set_value_bool(publish_agent):
    """
    Test setting a float value of a point through pubsub
    Format of expected result
    Header:
    {
    'requesterID': <Agent ID>
    }
    The message contains the value of the actuation point in JSON
    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_set_value_bool ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver3', point='SampleWritableBool1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver3', point='SampleWritableBool1')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()
    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=3))
    print ('start time for device1', start)

    msg = [
        ['fakedriver3', start, end]
    ]
    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_set_value_success1',
        'LOW',
        msg).get(timeout=10)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    assert result['result'] == 'SUCCESS'
    # set value
    header = {
        'requesterID': TEST_AGENT
    }

    publish_agent.vip.pubsub.publish('pubsub',
                                     topics.ACTUATOR_SET(campus='', building='', unit='fakedriver3',
                                                         point='SampleWritableBool1'),
                                     headers=header,
                                     message=True).get(timeout=10)
    gevent.sleep(1)
    try:
        print ('call args list', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == value_topic
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['requesterID'] == TEST_AGENT
        assert result_message == True
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_set_value_success1').get(timeout=10)


@pytest.mark.actuator_pubsub
def test_set_value_array(publish_agent):
    """
    Test setting point through pubsub. Set value as array with length=1
    Format of expected result
    Expected Header
    {
    'type': <'NEW_SCHEDULE', 'CANCEL_SCHEDULE'>
    'requesterID': <Agent ID from the request>,
    'taskID': <Task ID from the request>
    }
    Expected message
    {
    'result': <'SUCCESS', 'FAILURE', 'PREEMPTED'>,
    'info': <Failure reason, if any>,
    'data': <Data about the failure or cancellation, if any>
    }
    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_set_value_array ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver0', point='SampleWritableFloat1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver0', point='SampleWritableFloat1')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()
    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=3))
    print ('start time for device1', start)

    msg = [
        ['fakedriver1', start, end]
    ]
    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_set_value_success2',
        'LOW',
        msg).get(timeout=10)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    print result
    assert result['result'] == 'SUCCESS'
    # set value
    header = {
        'requesterID': TEST_AGENT
    }
    try:
        set_topic = topics.ACTUATOR_SET(campus='', building='', unit='fakedriver0', point='SampleWritableFloat1')
        print("set topic: ", set_topic)
        publish_agent.vip.pubsub.publish('pubsub',
                                         set_topic,
                                         headers=header,
                                         message=[0.2]).get(timeout=10)
        gevent.sleep(1)
        print ('call args list:', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == value_topic
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['requesterID'] == TEST_AGENT
        assert result_message == [0.2]
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_set_value_success2').get(timeout=10)


@pytest.mark.actuator_pubsub
def test_set_value_float(publish_agent):
    """
    Test setting a float value of a point  through pubsub.
    Value is set without enclosing it in an list
    Format of expected result
    Expected Header
    {
    'type': <'NEW_SCHEDULE', 'CANCEL_SCHEDULE'>
    'requesterID': <Agent ID from the request>,
    'taskID': <Task ID from the request>
    }
    Expected message
    {
    'result': <'SUCCESS', 'FAILURE', 'PREEMPTED'>,
    'info': <Failure reason, if any>,
    'data': <Data about the failure or cancellation, if any>
    }
    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_set_value_float ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver2', point='SampleWritableFloat1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver2', point='SampleWritableFloat1')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()
    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=3))
    print ('start time for device1', start)

    msg = [
        ['fakedriver2', start, end]
    ]
    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_set_value_success3',
        'LOW',
        msg).get(timeout=10)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    # print result
    assert result['result'] == 'SUCCESS'
    # set value
    header = {
        'requesterID': TEST_AGENT
    }

    set_topic = topics.ACTUATOR_SET(campus='', building='', unit='fakedriver2', point='SampleWritableFloat1')
    print("set topic: ", set_topic)
    publish_agent.vip.pubsub.publish('pubsub',
                                     set_topic,
                                     headers=header,
                                     message=0.2).get(timeout=10)
    gevent.sleep(1)
    try:
        print ('call args list ', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == value_topic
        result_header = publish_agent.callback.call_args[0][4]
        result_message = publish_agent.callback.call_args[0][5]
        assert result_header['requesterID'] == TEST_AGENT
        assert result_message == 0.2
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_set_value_success3').get(timeout=10)


@pytest.mark.actuator_pubsub
def test_set_read_only_point(publish_agent):
    """
    Test setting a value of a read only point through pubsub
    Format of expected result
    header:
    {
        'requesterID': <Agent ID>
    }
    message:
    {
        'type': <Error Type or name of the exception raised by the request>
        'value': <Specific info about the error>
    }

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_set_read_only_point ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver0', point='OutsideAirTemperature1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver0', point='OutsideAirTemperature1')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()
    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=3))
    print ('start time for device1', start)

    msg = [
        ['fakedriver0', start, end]
    ]
    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_set_read_only_point',
        'LOW',
        msg).get(timeout=10)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    # print result
    assert result['result'] == 'SUCCESS'
    # set value
    header = {
        'requesterID': TEST_AGENT
    }

    set_topic = topics.ACTUATOR_SET(campus='', building='', unit='fakedriver0', point='OutsideAirTemperature1')
    print("set topic: ", set_topic)
    publish_agent.vip.pubsub.publish('pubsub',
                                     set_topic,
                                     headers=header,
                                     message=['0.2']).get(timeout=10)
    publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_cancel_schedule',
        TEST_AGENT,
        'task_set_read_only_point').get(timeout=10)
    gevent.sleep(1)
    try:
        print ('call args list:', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        print ('call args ', publish_agent.callback.call_args[0])
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == error_topic
        result_header = publish_agent.callback.call_args[0][4]
        assert result_header['requesterID'] == TEST_AGENT
        result_message = publish_agent.callback.call_args[0][5]
        assert result_message['type'] == 'IOError'
        assert result_message['value'] == "['Trying to write to a point configured read only: OutsideAirTemperature1']"
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_set_read_only_point').get(timeout=10)


@pytest.mark.actuator_pubsub
def test_set_lock_error(publish_agent):
    """
    Test setting a float value of a point through pubsub without an allocation
    Format of expected result
    header:
    {
        'requesterID': <Agent ID>
    }
    message:
    {
        'type': 'LockError'
        'value': 'caller does not have this lock'
    }
    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_set_lock_error ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback_set_lock_error")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver1', point='SampleWritableFloat1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver1', point='SampleWritableFloat1')
    print('error topic:', error_topic)
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()

    # set value
    header = {
        'requesterID': TEST_AGENT
    }

    set_topic = topics.ACTUATOR_SET(campus='', building='', unit='fakedriver1', point='SampleWritableFloat1')
    print("set topic: ", set_topic)
    publish_agent.vip.pubsub.publish('pubsub',
                                     set_topic,
                                     headers=header,
                                     message=['0.2']).get(timeout=10)
    gevent.sleep(1)
    print ('call args list:', publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print ('call args ', publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == error_topic
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message['type'] == 'LockError'
    assert result_message['value'] == 'caller does not have this lock'


@pytest.mark.actuator_pubsub
def test_set_value_error(publish_agent):
    """
    Test setting a value of a point through pubsub
    Format of expected result
    header:
    {
        'requesterID': <Agent ID>
    }
    message:
    {
        'type': <Error Type or name of the exception raised by the request>
        'value': <Specific info about the error>
    }

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_set_value_error ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback_value_error")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver0', point='SampleWritableFloat1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver0', point='SampleWritableFloat1')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()
    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=3))
    print ('start time for device1', start)

    msg = [
        ['fakedriver0', start, end]
    ]
    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_value_error',
        'LOW',
        msg).get(timeout=10)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    print result
    assert result['result'] == 'SUCCESS'
    # set value
    header = {
        'requesterID': TEST_AGENT
    }

    set_topic = topics.ACTUATOR_SET(campus='', building='', unit='fakedriver0', point='SampleWritableFloat1')
    print("set topic: ", set_topic)
    publish_agent.vip.pubsub.publish('pubsub',
                                     set_topic,
                                     headers=header,
                                     message='abcd').get(timeout=10)
    gevent.sleep(1)
    try:
        print ('call args list:', publish_agent.callback.call_args_list)
        assert publish_agent.callback.call_count == 1
        print ('call args ', publish_agent.callback.call_args[0])
        assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
        assert publish_agent.callback.call_args[0][3] == error_topic
        result_header = publish_agent.callback.call_args[0][4]
        assert result_header['requesterID'] == TEST_AGENT
    finally:
        result = publish_agent.vip.rpc.call(
            'platform.actuator',
            'request_cancel_schedule',
            TEST_AGENT,
            'task_value_error').get(timeout=10)


# callback happens twice
@pytest.mark.actuator_pubsub
def test_get_value_success(publish_agent):
    """
    Test getting a float value of a point through pubsub
    Format of expected result
    Expected Header
    {
     'requesterID': <Agent ID from the request>,
     }
    Expected message - contains the value of the point

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_get_value_success ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver1', point='SampleWritableFloat1')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver1', point='SampleWritableFloat1')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()
    start = str(datetime.now())
    end = str(datetime.now() + timedelta(seconds=2))
    print ('start time for device1', start)

    msg = [
        ['fakedriver1', start, end]
    ]
    result = publish_agent.vip.rpc.call(
        'platform.actuator',
        'request_new_schedule',
        TEST_AGENT,
        'task_set_value_success2',
        'LOW',
        msg).get(timeout=10)
    # expected result {'info': u'', 'data': {}, 'result': 'SUCCESS'}
    print result
    assert result['result'] == 'SUCCESS'

    # set value
    header = {
        'requesterID': TEST_AGENT
    }

    result = publish_agent.vip.rpc.call(
        'platform.actuator',  # Target agent
        'set_point',  # Method
        TEST_AGENT,  # Requestor
        'fakedriver1/SampleWritableFloat1',  # Point to set
        20.5  # New value
    ).get(timeout=10)
    print ("result of set", result)
    get_topic = topics.ACTUATOR_GET(campus='', building='', unit='fakedriver1', point='SampleWritableFloat1')
    print("set topic: ", get_topic)
    publish_agent.vip.pubsub.publish('pubsub',
                                     get_topic,
                                     headers=header).get(timeout=10)
    print("call args list", publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print ('call args ', publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == value_topic
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_header['requesterID'] == TEST_AGENT
    assert result_message == 20.5


# error gets sent to value topic

@pytest.mark.actuator_pubsub
def test_get_invalid_point(publish_agent):
    """
    Test getting a float value of a point through pubsub with invalid point name
    Format of expected result
    Expected Header
    {
     'requesterID': <Agent ID from the request>,
    }
    Expected message
    {
    'type': <Error Type or name of the exception raised by the request>
    'value': <Specific info about the error>
    }

    :param publish_agent: fixture invoked to setup all agents necessary and returns an instance
    of Agent object used for publishing
    """
    print ("**** test_get_invalid_point ****")
    # Mock callback methods
    publish_agent.callback = MagicMock(name="callback")
    # Subscribe to result of set
    value_topic = topics.ACTUATOR_VALUE(campus='', building='', unit='fakedriver1', point='SampleWritableFloat12')
    error_topic = topics.ACTUATOR_ERROR(campus='', building='', unit='fakedriver1', point='SampleWritableFloat12')
    print ('value topic', value_topic)
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=value_topic,
                                       callback=publish_agent.callback).get()
    publish_agent.vip.pubsub.subscribe(peer='pubsub',
                                       prefix=error_topic,
                                       callback=publish_agent.callback).get()

    header = {
        'requesterID': TEST_AGENT
    }
    get_topic = topics.ACTUATOR_GET(campus='', building='', unit='fakedriver1', point='SampleWritableFloat12')
    print("set topic: ", get_topic)
    publish_agent.vip.pubsub.publish('pubsub',
                                     get_topic,
                                     headers=header).get(timeout=10)
    gevent.sleep(1)
    print("call args list", publish_agent.callback.call_args_list)
    assert publish_agent.callback.call_count == 1
    print ('call args ', publish_agent.callback.call_args[0])
    assert publish_agent.callback.call_args[0][1] == PLATFORM_ACTUATOR
    assert publish_agent.callback.call_args[0][3] == error_topic
    result_header = publish_agent.callback.call_args[0][4]
    result_message = publish_agent.callback.call_args[0][5]
    assert result_message['type'] == 'master_driver.interfaces.DriverInterfaceError'
    assert result_message['value'] == "['Point not configured on device: SampleWritableFloat12']"
    assert result_header['requesterID'] == TEST_AGENT