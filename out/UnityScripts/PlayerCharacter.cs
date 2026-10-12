using UnityEngine;

/// <summary>
/// 由 UE 的 APlayerCharacter 迁移而来。
///
/// 最大的架构差异: UE 的 ACharacter 自带胶囊体 + CharacterMovementComponent,
/// 重力、跳跃、落地检测、移动转身全部由引擎代劳。
/// Unity 的 CharacterController 只提供"带碰撞的位移", 其余都得自己写,
/// 所以这个类的代码量比 UE 版多, 但责任边界反而更清楚。
/// </summary>
[RequireComponent(typeof(CharacterController))]
public class PlayerCharacter : MonoBehaviour
{
    /// <summary>对应 UE 的 EMovementState。</summary>
    public enum MovementState
    {
        Walking,
        Sprinting,
        Jumping,
        Climbing,
    }

    [Header("移动 (UE 原值 x0.01: 400cm/s -> 4m/s)")]
    [SerializeField] private float walkSpeed = 4f;          // UE WalkSpeed = 400
    [SerializeField] private float sprintSpeed = 7.5f;      // UE SprintSpeed = 750
    [SerializeField] private float climbSpeed = 2f;         // UE ClimbSpeed = 200

    [Tooltip("跳跃高度。由 UE 的 JumpZVelocity 反推: h = v^2 / (2g) = 6^2 / (2*17.15) ≈ 1.05m")]
    [SerializeField] private float jumpHeight = 1.05f;

    [Tooltip("UE GravityScale = 1.75, 世界重力约 980cm/s^2 -> 9.8 * 1.75")]
    [SerializeField] private float gravity = 17.15f;

    [Tooltip("空中水平输入系数, 对应 UE 的 AirControl")]
    [Range(0f, 1f)]
    [SerializeField] private float airControl = 0.35f;

    [Header("转身 (对应 UE 的 bOrientRotationToMovement / RotationRate)")]
    [SerializeField] private bool characterFollowsCameraYaw = true;  // UE bUseControllerRotationYaw
    [SerializeField] private bool orientToMovement = true;           // UE bOrientRotationToMovement
    [SerializeField] private float turnSpeed = 12f;                  // UE RotationRate 640 度/秒, Unity 侧改用 Slerp 系数

    [Header("视角")]
    [SerializeField] private float mouseSensitivity = 1f;
    [SerializeField] private float minPitch = -70f;
    [SerializeField] private float maxPitch = 70f;
    [SerializeField] private bool invertY;

    [Header("相机 (UE: 300cm 臂长 / SocketOffset(0,40,70)cm)")]
    [SerializeField] private Transform cameraPivot;
    [SerializeField] private Transform cameraTransform;
    [SerializeField] private float armLength = 3f;
    [Tooltip("Z-up -> Y-up 轴重映射: UE(X前,Y右,Z上) -> Unity(X右,Y上,Z前)")]
    [SerializeField] private Vector3 socketOffset = new Vector3(0.4f, 0.7f, 0f);
    [SerializeField] private float cameraProbeRadius = 0.2f;
    [SerializeField] private LayerMask cameraCollisionMask = ~0;

    [Header("按键 (UE 项目里的 Action 映射, 需要在 Unity 侧对齐)")]
    [SerializeField] private KeyCode jumpKey = KeyCode.Space;
    [SerializeField] private KeyCode sprintKey = KeyCode.LeftShift;
    [SerializeField] private KeyCode climbKey = KeyCode.LeftControl;
    [SerializeField] private bool lockCursorOnStart = true;

    [Header("组件引用")]
    [SerializeField] private WallClimbComponent climbComponent;

    private CharacterController controller;
    private MovementState movementState = MovementState.Walking;

    private Vector2 moveInput;
    private Vector3 velocity;          // UE 有 CharacterMovement->Velocity, Unity 必须自己维护
    private Vector3 climbSurfaceNormal = Vector3.zero;
    private bool climbInputHeld;
    private bool wasGroundedLastFrame;
    private float yaw;
    private float pitch;

    public MovementState State => movementState;
    public Vector3 Velocity => velocity;

    private void Awake()
    {
        controller = GetComponent<CharacterController>();

        // Unity 不能像 UE 的 CreateDefaultSubobject 那样在构造函数里造组件,
        // 所以这里用"Inspector 优先, 代码兜底"的写法, 保证脚本拖上去就能跑。
        if (climbComponent == null)
        {
            climbComponent = GetComponent<WallClimbComponent>();
        }

        if (cameraPivot == null)
        {
            cameraPivot = transform;
        }

        if (cameraTransform == null && Camera.main != null)
        {
            cameraTransform = Camera.main.transform;
        }
    }

    private void Start()
    {
        // 对应 UE BeginPlay
        yaw = transform.eulerAngles.y;
        movementState = MovementState.Walking;
        wasGroundedLastFrame = controller.isGrounded;

        if (lockCursorOnStart)
        {
            // 对应 UE 的 bShowMouseCursor = false
            Cursor.lockState = CursorLockMode.Locked;
            Cursor.visible = false;
        }

        Debug.Log($"PlayerCharacter ready. walkSpeed={walkSpeed} sprintSpeed={sprintSpeed}");
    }

    private void Update()
    {
        // 对应 UE Tick(DeltaTime)
        ReadLookInput();
        ReadMoveInput();
        UpdateMovementState();

        if (movementState == MovementState.Climbing)
        {
            ApplyClimbMovement(Time.deltaTime);
        }
        else
        {
            ApplyWalkMovement(Time.deltaTime);
        }

        HandleJumpAndClimbInput();
    }

    private void LateUpdate()
    {
        UpdateCameraBoom();
    }

    // ------------------------------------------------------------------
    // 输入
    // ------------------------------------------------------------------

    private void ReadLookInput()
    {
        // 对应 UE TurnAtRate / LookUpAtRate (BindAxis + AddControllerYawInput/PitchInput)
        float mouseX = Input.GetAxis("Mouse X") * mouseSensitivity;
        float mouseY = Input.GetAxis("Mouse Y") * mouseSensitivity * (invertY ? 1f : -1f);

        yaw += mouseX;

        // UE 的 Pitch 正值是抬头, Unity 的 euler.x 正值是低头, 所以这里取负。
        pitch = Mathf.Clamp(pitch + mouseY, minPitch, maxPitch);
    }

    private void ReadMoveInput()
    {
        // 对应 UE BindAxis("MoveForward"/"MoveRight") 的输入采集部分。
        // Unity 内置 Horizontal/Vertical 轴值域是 [-1,1], 没有 UE 那种靠轴放大倍率
        // 传额外信息(原工程用 MoveForward > 1.5 判断冲刺)的能力, 所以冲刺改成独立按键。
        moveInput = new Vector2(Input.GetAxisRaw("Horizontal"), Input.GetAxisRaw("Vertical"));

        if (climbInputHeld && !Input.GetKey(climbKey))
        {
            climbInputHeld = false;
            OnClimbReleased();
        }
        else if (!climbInputHeld && Input.GetKeyDown(climbKey))
        {
            climbInputHeld = true;
            OnClimbPressed();
        }
    }

    private void HandleJumpAndClimbInput()
    {
        if (Input.GetKeyDown(jumpKey))
        {
            OnJumpPressed();
        }
        else if (Input.GetKeyUp(jumpKey))
        {
            OnJumpReleased();
        }
    }

    // ------------------------------------------------------------------
    // 状态机 (UE 里由 CharacterMovementComponent 的 MovementMode 隐式管理)
    // ------------------------------------------------------------------

    private void UpdateMovementState()
    {
        if (movementState == MovementState.Climbing)
        {
            // 爬墙过程中墙面消失(走出边缘 / 超过高度上限)则自动脱离
            if (climbComponent == null || !climbComponent.IsOnClimbableSurface())
            {
                OnClimbReleased();
            }
            return;
        }

        bool grounded = IsGrounded();

        // Unity 没有 Landed() 回调, 用前后两帧的地面状态对比补出来
        if (!wasGroundedLastFrame && grounded)
        {
            OnLanded();
        }
        wasGroundedLastFrame = grounded;

        if (movementState == MovementState.Walking || movementState == MovementState.Sprinting)
        {
            float horizontalSpeed = new Vector2(velocity.x, velocity.z).magnitude;
            if (horizontalSpeed > 0.1f)
            {
                movementState = horizontalSpeed > walkSpeed + 0.1f
                    ? MovementState.Sprinting
                    : MovementState.Walking;
            }
            else
            {
                movementState = MovementState.Walking;
            }
        }
        else if (movementState == MovementState.Jumping && grounded)
        {
            movementState = MovementState.Walking;
        }
    }

    private bool IsGrounded()
    {
        // isGrounded 有粘滞: 离地后仍可能保持 true 一两帧, 所以要配合纵向速度判断
        return controller.isGrounded && velocity.y <= 0.01f;
    }

    private void OnLanded()
    {
        // UE 的 Landed() 回调在这里补出来
        movementState = MovementState.Walking;
    }

    // ------------------------------------------------------------------
    // 移动
    // ------------------------------------------------------------------

    private void ApplyWalkMovement(float deltaTime)
    {
        if (movementState == MovementState.Climbing)
        {
            return;
        }

        // UE 的 CharacterMovement 自动处理重力, Unity 必须自己积分。
        // 贴地时给一个小的向下速度, 避免 isGrounded 在斜坡上反复抖动。
        if (controller.isGrounded && velocity.y < 0f)
        {
            velocity.y = -2f;
        }
        else
        {
            velocity.y -= gravity * deltaTime;
        }

        // 对应 UE MoveForward/MoveRight: 方向取自控制器 Yaw, 而不是角色自身朝向
        Quaternion yawRotation = Quaternion.Euler(0f, yaw, 0f);
        Vector3 worldDirection = yawRotation * new Vector3(moveInput.x, 0f, moveInput.y);
        if (worldDirection.sqrMagnitude > 1f)
        {
            worldDirection.Normalize();
        }

        float targetSpeed = (moveInput.y > 0f && Input.GetKey(sprintKey)) ? sprintSpeed : walkSpeed;
        if (movementState == MovementState.Climbing)
        {
            targetSpeed = climbSpeed;
        }

        float control = controller.isGrounded ? 1f : airControl;
        Vector3 horizontal = worldDirection * (targetSpeed * control);
        velocity.x = horizontal.x;
        velocity.z = horizontal.z;

        controller.Move(velocity * deltaTime);

        ApplyRotation(worldDirection);
    }

    private void ApplyRotation(Vector3 worldDirection)
    {
        if (characterFollowsCameraYaw && movementState != MovementState.Climbing)
        {
            transform.rotation = Quaternion.Euler(0f, yaw, 0f);
            return;
        }

        if (orientToMovement && worldDirection.sqrMagnitude > 0.001f)
        {
            // UE 的 bOrientRotationToMovement + RotationRate
            Quaternion target = Quaternion.LookRotation(worldDirection, Vector3.up);
            transform.rotation = Quaternion.Slerp(transform.rotation, target, turnSpeed * Time.deltaTime);
        }
    }

    private void ApplyClimbMovement(float deltaTime)
    {
        if (!climbInputHeld)
        {
            return;
        }

        // 沿墙面竖直向上攀爬, 同时用一点向墙内的推力保持贴合。
        // UE 的 AddActorWorldOffset(带 sweep) 对应 CharacterController.Move。
        Vector3 upAlongWall = Vector3.up * climbSpeed;
        Vector3 intoWall = -climbSurfaceNormal * 0.3f;  // UE 30cm/s -> 0.3m/s
        Vector3 lateral = Vector3.Cross(climbSurfaceNormal, Vector3.up) * moveInput.x * climbSpeed;

        velocity = Vector3.zero;  // 爬墙时不受重力影响, 对应 UE 的 MOVE_Flying
        controller.Move((upAlongWall + intoWall + lateral) * deltaTime);
    }

    // ------------------------------------------------------------------
    // 跳跃与爬墙
    // ------------------------------------------------------------------

    private void OnJumpPressed()
    {
        if (movementState == MovementState.Climbing)
        {
            // 爬墙中按跳跃 = 蹬墙跳开 (对应 UE 的 LaunchCharacter)
            if (climbComponent != null)
            {
                climbComponent.StopClimb();
            }

            movementState = MovementState.Jumping;
            velocity = climbSurfaceNormal * 5f + Vector3.up * 4f;  // UE 500/400 cm/s -> 5/4 m/s
            return;
        }

        // UE 的 Jump() 由 CharacterMovement 处理; Unity 侧要自己给纵向初速度。
        // 这里由 jumpHeight 反推, 结果与 UE 的 JumpZVelocity=600cm/s (6m/s) 等价。
        if (IsGrounded())
        {
            velocity.y = Mathf.Sqrt(2f * jumpHeight * gravity);
            movementState = MovementState.Jumping;
        }
    }

    private void OnJumpReleased()
    {
        // 对应 UE StopJumping(): Unity 没有内置跳跃, 用"松手切断上升"实现可变跳跃高度
        if (velocity.y > 0f)
        {
            velocity.y *= 0.5f;
        }
    }

    private void OnClimbPressed()
    {
        if (movementState == MovementState.Walking
            || movementState == MovementState.Sprinting
            || movementState == MovementState.Jumping)
        {
            if (climbComponent != null && climbComponent.TryStartClimb())
            {
                movementState = MovementState.Climbing;
                climbSurfaceNormal = climbComponent.SurfaceNormal;
                velocity = Vector3.zero;  // 对应 UE 的 SetMovementMode(MOVE_Flying) + 关闭重力
                orientToMovement = false; // 对应 UE 关闭 bOrientRotationToMovement
                FaceWall();
            }
            else
            {
                Debug.Log("前方没有可攀爬的墙面");
            }
        }
    }

    private void OnClimbReleased()
    {
        if (movementState != MovementState.Climbing)
        {
            return;
        }

        if (climbComponent != null)
        {
            climbComponent.StopClimb();
        }

        movementState = MovementState.Jumping;
        climbSurfaceNormal = Vector3.zero;
        orientToMovement = true;   // 对应 UE 恢复 bOrientRotationToMovement
        velocity.y = 0f;           // 恢复重力作用, 从静止开始下落
    }

    private void FaceWall()
    {
        // 爬墙时面朝墙面(法线反向)
        Vector3 facing = -climbSurfaceNormal;
        if (facing.sqrMagnitude > 0.001f)
        {
            transform.rotation = Quaternion.LookRotation(facing, Vector3.up);
        }
    }

    // ------------------------------------------------------------------
    // 相机
    // ------------------------------------------------------------------

    private void UpdateCameraBoom()
    {
        if (cameraPivot == null)
        {
            return;
        }

        // UE 的 SpringArm 由 bUsePawnControlRotation 决定是否吃控制器俯仰角;
        // Unity 没有 SpringArm, 这里用一个枢轴 Transform 自己搭。
        cameraPivot.rotation = Quaternion.Euler(pitch, yaw, 0f);

        if (cameraTransform == null)
        {
            return;
        }

        Vector3 pivotPosition = cameraPivot.position + cameraPivot.rotation * socketOffset;

        // 弹簧臂朝向枢轴后方. Unity 的 forward 是 +Z, 所以相机在 -Z 方向
        Vector3 armDirection = -(cameraPivot.rotation * Vector3.forward);

        // 对应 UE 的 bDoCollisionTest: Unity 必须手写, 用 SphereCast 撞到就收臂
        float targetArmLength = armLength;
        if (Physics.SphereCast(
                pivotPosition,
                cameraProbeRadius,
                armDirection,
                out RaycastHit hit,
                armLength,
                cameraCollisionMask,
                QueryTriggerInteraction.Ignore))
        {
            targetArmLength = Mathf.Max(hit.distance - cameraProbeRadius, 0.2f);
        }

        cameraTransform.position = pivotPosition + armDirection * targetArmLength;
        cameraTransform.rotation = cameraPivot.rotation;
    }
}
